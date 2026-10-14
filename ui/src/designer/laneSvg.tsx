import { useContext, useState } from "react";
import { AutomationMenu } from "./automation";
import { PLACE_MIME, itemOf, placeable, rowOf } from "./clipboard";
import type { Placeable } from "./clipboard";
import type { Edits, History, RowsDoc } from "./history";
import { ParamLanes } from "./laneSpecs";
import { barBeat, itemName, itemSub, uniqueId } from "./model";
import type { Item, Row } from "./model";

/**
 * A lane of clips, drawn and edited: its items, its gap rule, its menu,
 * and adding a lane.
 */
export const LANE_H = 40;

export function LaneSvg({ row, x, width, zoom, selected, onSelect, history, onMenu, onDropItem, onAdd,
                   noun }: {
  row: Row; x: (b: number) => number; width: number; zoom: number;
  selected: string | null; onSelect: (id: string | null) => void; history: Edits;
  /** A clip's menu, asked for with the other mouse button. */
  onMenu?: (id: string, clientX: number, clientY: number) => void;
  /** Something dragged from the browser, let go at a beat on this lane. */
  onDropItem?: (what: Placeable, beat: number) => void;
  /** A click on the lane's empty space, to add something there: at the beat
   *  clicked, or null for the playhead (Enter, with the lane focused). */
  onAdd?: (beat: number | null, clientX: number, clientY: number) => void;
  /** What a click adds, for an empty lane to say: "a block", "a routine". */
  noun?: string;
}) {
  const menu = (e: React.MouseEvent, id: string) => {
    if (!onMenu) return;
    e.preventDefault();
    onSelect(id);
    onMenu(id, e.clientX, e.clientY);
  };
  // A drag is shown live from local state and committed as ONE edit on release.
  const [drag, setDrag] = useState<{ id: string; mode: "move" | "resize";
                                     start: number; at: number; len: number;
                                     dAt: number; dLen: number } | null>(null);
  const hits = row.type === "hits";
  const items = row.items ?? [];

  const begin = (e: React.PointerEvent, it: Item, mode: "move" | "resize") => {
    e.stopPropagation();
    onSelect(it.id);
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setDrag({ id: it.id, mode, start: e.clientX, at: it.at, len: it.len, dAt: 0, dLen: 0 });
  };
  const move = (e: React.PointerEvent) => {
    if (!drag) return;
    const beats = (e.clientX - drag.start) / zoom;
    if (drag.mode === "move") {
      setDrag({ ...drag, dAt: history.snapBeat(drag.at + beats) - drag.at });
    } else {
      const end = history.snapBeat(drag.at + drag.len + beats);
      setDrag({ ...drag, dLen: Math.max(history.snap === "beat" ? 1 : 4, end - drag.at) - drag.len });
    }
  };
  const end = () => {
    if (!drag) return;
    const { id, dAt, dLen } = drag;
    setDrag(null);
    if (!dAt && !dLen) return;
    history.apply((d) => {
      const it = itemOf(d, id);
      if (!it) return;
      it.at = it.at + dAt;
      it.len = it.len + dLen;
      if ((it.fade ?? 0) > it.len) it.fade = it.len;
    });
  };

  const addable = noun ?? (hits ? "a hit" : "a block");
  return (
    <svg width={width} height={LANE_H} className={`d-lane${onAdd ? " d-addable" : ""}`}
         aria-label={`lane ${row.id}`}
         tabIndex={onAdd ? 0 : undefined}
         aria-keyshortcuts={onAdd ? "Enter" : undefined}
         onPointerMove={move} onPointerUp={end} onPointerCancel={end}
         onClick={(e) => {
           if (e.target !== e.currentTarget) return;
           onSelect(null);
           if (onAdd) {
             const r = e.currentTarget.getBoundingClientRect();
             onAdd(Math.max(0, (e.clientX - r.left) / zoom), e.clientX, e.clientY);
           }
         }}
         onKeyDown={(e) => {
           if (!onAdd || e.key !== "Enter" || e.target !== e.currentTarget) return;
           e.preventDefault();
           const r = e.currentTarget.getBoundingClientRect();
           onAdd(null, r.left, r.bottom);
         }}
         onDragOver={(e) => {
           if (onDropItem && e.dataTransfer.types.includes(PLACE_MIME)) e.preventDefault();
         }}
         onDrop={(e) => {
           const what = placeable(e.dataTransfer.getData(PLACE_MIME));
           if (!onDropItem || !what) return;
           e.preventDefault();
           const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
           onDropItem(what, Math.max(0, (e.clientX - r.left) / zoom));
         }}>
      {onAdd && !items.length && (
        <text x={8} y={LANE_H / 2 + 4} className="d-lane-hint" pointerEvents="none">
          Empty -- click to add {addable}</text>)}
      {items.map((it) => {
        const live = drag?.id === it.id ? drag : null;
        const at = it.at + (live?.dAt ?? 0);
        const len = it.len + (live?.dLen ?? 0);
        const sel = selected === it.id;
        if (hits) {
          const cx = x(at);
          return (
            <g key={it.id} className={`d-hit d-hit-${it.hit}${sel ? " sel" : ""}`}
               onPointerDown={(e) => begin(e, it, "move")} role="button"
               onContextMenu={(e) => menu(e, it.id)}
               aria-label={`${it.hit} at bar ${barBeat(at)}`}>
              <rect x={cx} y={LANE_H / 2 - 3} width={Math.max(2, x(len) - x(0))} height={6}
                    rx={3} className="d-hit-span" />
              <path d={`M${cx} ${LANE_H / 2 - 9} l7 9 l-7 9 l-7 -9 z`} />
            </g>
          );
        }
        const w = Math.max(3, x(len) - x(0) - 1);
        const fadeW = Math.min(w, x(it.fade ?? 0) - x(0));
        return (
          <g key={it.id} className={`d-clip d-clip-${it.kind ?? "block"}${sel ? " sel" : ""}`}
             role="button" aria-label={`${itemName(it)} at bar ${barBeat(at)}`}
             onContextMenu={(e) => menu(e, it.id)}>
            <rect x={x(at)} y={2} width={w} height={LANE_H - 4} rx={4}
                  onPointerDown={(e) => begin(e, it, "move")} />
            {fadeW > 0 && (
              <path d={`M${x(at)} ${LANE_H - 2} L${x(at) + fadeW} 2 L${x(at)} 2 z`}
                    className="d-fade" pointerEvents="none" />)}
            <text x={x(at) + 5} y={16} className="d-clip-name" pointerEvents="none">
              {itemName(it)}</text>
            <text x={x(at) + 5} y={30} className="d-clip-sub" pointerEvents="none">
              {itemSub(it)}</text>
            <rect x={x(at) + w - 6} y={2} width={6} height={LANE_H - 4}
                  className="d-resize" aria-label={`resize ${it.id}`}
                  onPointerDown={(e) => begin(e, it, "resize")} />
          </g>
        );
      })}
    </svg>
  );
}

export function GapToggle({ row, history }: { row: Row; history: Edits }) {
  const owns = row.gap === "exclusive";
  return (
    <button className={`small d-gap${owns ? " on" : ""}`}
            title={owns ? "This lane owns the track: in its gaps nothing drives it"
              : "Template fills gaps: the lanes below and the template show through"}
            onClick={() => history.apply((d) => {
              const r = rowOf(d, row.id);
              if (r) r.gap = owns ? "fill" : "exclusive";
            })}>
      {owns ? "owns track" : "fills gaps"}
    </button>
  );
}

export function LaneMenu({ row, index, history }: { row: Row; index: number; history: Edits }) {
  const move = (delta: number) => history.apply((d) => {
    const i = d.rows.findIndex((r) => r.id === row.id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= d.rows.length) return;
    const [r] = d.rows.splice(i, 1);
    d.rows.splice(j, 0, r!);
  });
  return (
    <span className="d-lane-menu">
      {index >= 0 && <>
        <button className="small" aria-label={`move ${row.id} up`} onClick={() => move(-1)}>↑</button>
        <button className="small" aria-label={`move ${row.id} down`} onClick={() => move(1)}>↓</button>
      </>}
      <button className="small" aria-label={`remove lane ${row.id}`}
              onClick={() => history.apply((d) => { d.rows = d.rows.filter((r) => r.id !== row.id); })}>
        ×</button>
    </span>
  );
}

const NEW_LANES: [string, string][] = [
  ["scene", "Scene"], ["movement", "Movement"], ["color", "Color"], ["level", "Level"],
  ["palette", "Palette"], ["hits", "Hits"], ["osc", "OSC cues"], ["osc-curve", "OSC curve"],
  ["midi", "MIDI cues"], ["midi-curve", "MIDI curve"], ["visuals", "Visuals"],
];

/** A new lane for another output (milestone 3), or undefined for a lights lane. */
export function externalRow(d: RowsDoc, kind: string): Row | undefined {
  if (kind === "osc") return { id: uniqueId(d, "osc"), type: "external", output: "osc", items: [] };
  if (kind === "osc-curve") {
    return { id: uniqueId(d, "osc-curve"), type: "external", output: "osc",
             address: "/composition/layers/1/video/opacity", args: ["$value"],
             points: [[0, 1]] };
  }
  if (kind === "midi") return { id: uniqueId(d, "midi"), type: "external", output: "midi", items: [] };
  if (kind === "visuals") {
    return { id: uniqueId(d, "visuals"), type: "external", output: "visuals", items: [] };
  }
  if (kind === "midi-curve") {
    return { id: uniqueId(d, "midi-curve"), type: "external", output: "midi",
             channel: 1, cc: 1, points: [[0, 0]] };
  }
  return undefined;
}

export function AddLane({ history }: { history: History }) {
  const params = useContext(ParamLanes);
  const doc = history.doc;
  if (!doc) return null;
  return (
    <div className="d-row d-add">
      <div className="d-head">
        <select aria-label="add lane" value=""
                onChange={(e) => {
                  const target = e.target.value;
                  if (!target) return;
                  history.apply((d) => {
                    const external = externalRow(d, target);
                    if (external) { d.rows.push(external); return; }
                    const id = uniqueId(d, target);
                    d.rows.push(target === "hits"
                      ? { id, type: "hits", items: [] }
                      : { id, type: "clips", target, gap: target === "palette" ? "exclusive" : "fill",
                          items: [] });
                  });
                }}>
          <option value="">+ lane</option>
          {NEW_LANES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <AutomationMenu history={history} params={params} />
      </div>
    </div>
  );
}
