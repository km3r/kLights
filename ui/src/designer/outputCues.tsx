import { useEffect, useState } from "react";
import { PALETTE_ROLES } from "../showfiles.generated";
import { apiFetch } from "../useEngine";
import { newVisuals } from "./items";
import { VISUAL_PARAMS, VISUAL_SCENES, oscArgsText, parseOscArgs } from "./model";
import type { Item, OscMessage, VisualRule } from "./model";

/**
 * Cues for what is not lights: an OSC message, a MIDI event, a visuals scene.
 */
const OSC_WHEN: Record<"on" | "while" | "off", string> = {
  on: "When it starts", while: "While it plays (on change, 30/s at most)",
  off: "When it ends",
};

/** An OSC cue's three messages, for the track and routine inspectors. */
export function OscCue({ item, set }: { item: Item; set: (fields: Partial<Item>) => void }) {
  return (
    <>
      {(["on", "while", "off"] as const).map((k) => (
        <OscField key={`${item.id}-${k}`} which={k} message={item[k]}
                  onChange={(m) => set({ [k]: m })} />))}
    </>
  );
}

const ROLE_COLORS = PALETTE_ROLES.map((r) => `@${r}`);

/** A visuals cue: its scene, and that scene's parameters. A video's file is
 *  picked from the show folder's media/. */
export function VisualCue({ item, set }: { item: Item; set: (fields: Partial<Item>) => void }) {
  const [media, setMedia] = useState<string[] | null>(null);
  const scene = item.scene ?? "wash";
  useEffect(() => {
    if (scene !== "video") return;
    let live = true;
    apiFetch<{ media: { file: string }[] }>("/api/media")
      .then((r) => { if (live) setMedia(r.media.map((m) => m.file)); })
      .catch(() => { if (live) setMedia([]); });
    return () => { live = false; };
  }, [scene]);
  const params = item.params ?? {};
  const setParam = (name: string, value: unknown) => {
    const next = { ...params };
    if (value === undefined || value === "") delete next[name]; else next[name] = value;
    set({ params: next });
  };
  const rules: Record<string, VisualRule> = { ...(VISUAL_PARAMS[scene] ?? {}), opacity: [0, 1] };
  return (
    <>
      <div className="d-chips" role="group" aria-label="visuals scene">
        {VISUAL_SCENES.map((s) => (
          <button key={s} className={scene === s ? "on" : ""}
                  onClick={() => set(newVisuals(s))}>
            {s}</button>))}
      </div>
      {Object.entries(rules).map(([name, rule]) => {
        const value = params[name];
        const label = `visuals ${name}`;
        if (rule === "color") {
          const role = typeof value === "string" && value.startsWith("@") ? value : "";
          return (
            <label key={name} className="small">{name}{" "}
              <select aria-label={label} value={role || (typeof value === "string" ? "hex" : "")}
                      onChange={(e) => setParam(name, e.target.value === "hex"
                        ? "#ffffff" : e.target.value || undefined)}>
                <option value="">(default)</option>
                {ROLE_COLORS.map((r) => <option key={r} value={r}>{r}</option>)}
                <option value="hex">a color…</option>
              </select>
              {typeof value === "string" && value.startsWith("#") && (
                <input type="color" aria-label={`${label} color`} value={value}
                       onChange={(e) => setParam(name, e.target.value)} />)}
            </label>
          );
        }
        if (rule === "file") {
          const files = media ?? [];
          return (
            <label key={name} className="small">file{" "}
              <select aria-label={label} value={typeof value === "string" ? value : ""}
                      onChange={(e) => setParam(name, e.target.value || undefined)}>
                <option value="">{media == null ? "…" : files.length ? "(pick one)" : "(media/ is empty)"}</option>
                {typeof value === "string" && value && !files.includes(value) && (
                  <option value={value}>{value} (not in media/)</option>)}
                {files.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </label>
          );
        }
        if (rule === "bool") {
          return (
            <label key={name} className="small">
              <input type="checkbox" aria-label={label} checked={value !== false}
                     onChange={(e) => setParam(name, e.target.checked)} /> {name}
            </label>
          );
        }
        if (typeof rule[0] === "string") {
          return (
            <label key={name} className="small">{name}{" "}
              <select aria-label={label} value={typeof value === "string" ? value : ""}
                      onChange={(e) => setParam(name, e.target.value || undefined)}>
                <option value="">(default)</option>
                {(rule as string[]).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
          );
        }
        const [lo, hi] = rule as [number, number];
        return (
          <label key={name} className="small">{name}{" "}
            <input type="number" className="d-num" min={lo} max={hi} step={hi <= 1 ? 0.05 : 1}
                   aria-label={label} placeholder="default"
                   value={typeof value === "number" ? value : ""}
                   onChange={(e) => setParam(name, e.target.value === ""
                     ? undefined : Number(e.target.value))} />
          </label>
        );
      })}
    </>
  );
}

const MIDI_KINDS = { note: "Note", cc: "CC", pc: "Program" } as const;

/** A MIDI cue: a note held for its length, a CC (and the value it leaves
 *  behind), or a program change -- on a channel. */
export function MidiCue({ item, set }: { item: Item; set: (fields: Partial<Item>) => void }) {
  const kind = item.note != null ? "note" : item.cc != null ? "cc" : "pc";
  const num = (label: string, field: keyof Item, value: number | undefined,
               lo: number, hi: number, optional = false) => (
    <label className="small">{label}{" "}
      <input type="number" className="d-num" min={lo} max={hi} aria-label={`midi ${field}`}
             value={value ?? ""} placeholder={optional ? "none" : undefined}
             onChange={(e) => set({ [field]: e.target.value === "" && optional
               ? undefined : Number(e.target.value) })} />
    </label>
  );
  return (
    <>
      <div className="d-chips" role="group" aria-label="midi kind">
        {(Object.keys(MIDI_KINDS) as (keyof typeof MIDI_KINDS)[]).map((k) => (
          <button key={k} className={kind === k ? "on" : ""}
                  onClick={() => set({
                    note: k === "note" ? 60 : undefined, velocity: k === "note" ? 100 : undefined,
                    cc: k === "cc" ? 1 : undefined, value: k === "cc" ? 127 : undefined,
                    off_value: undefined, pc: k === "pc" ? 0 : undefined })}>
            {MIDI_KINDS[k]}</button>))}
      </div>
      {num("Channel", "channel", item.channel ?? 1, 1, 16)}
      {kind === "note" && <>{num("Note", "note", item.note, 0, 127)}
        {num("Velocity", "velocity", item.velocity ?? 100, 1, 127)}</>}
      {kind === "cc" && <>{num("CC", "cc", item.cc, 0, 127)}
        {num("Value", "value", item.value ?? 127, 0, 127)}
        {num("Then", "off_value", item.off_value, 0, 127, true)}</>}
      {kind === "pc" && num("Program", "pc", item.pc, 0, 127)}
    </>
  );
}

/** One OSC message of a cue: its address and arguments, or none. */
function OscField({ which, message, onChange }: {
  which: "on" | "while" | "off"; message?: OscMessage;
  onChange: (m: OscMessage | undefined) => void;
}) {
  if (!message) {
    return (
      <div>
        <span className="small muted">{OSC_WHEN[which]}</span>
        <div><button className="small" onClick={() => onChange({ address: "/", args: [] })}>
          + {which} message</button></div>
      </div>
    );
  }
  return (
    <div className="d-osc">
      <span className="small muted">{OSC_WHEN[which]}</span>
      <input className="mono" aria-label={`${which} address`} value={message.address}
             onChange={(e) => onChange({ ...message, address: e.target.value })} />
      <input className="mono" aria-label={`${which} args`}
             defaultValue={oscArgsText(message.args)}
             placeholder="1, $bar, $progress"
             onBlur={(e) => onChange({ ...message, args: parseOscArgs(e.target.value) })} />
      <button className="small" aria-label={`remove ${which} message`}
              onClick={() => onChange(undefined)}>×</button>
    </div>
  );
}
