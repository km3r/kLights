"""Generates despacio/webui/ui_config.js (the mobile Virtual Console's data
file) from despacio.qxw, and lints despacio/webui/ui_layout.js against it.

Design invariants this generator enforces -- see despacio/README.md and the
webui plan for the reasoning:

  * Never expose the ~185 hidden mirror buttons that implement the SoloFrame
    exclusivity mesh (COLOR/MOVEMENT families). Only real, user-facing
    widgets -- caption not starting with "~" -- go in the output. The phone
    UI drives *widget* IDs, never function IDs, so pressing one still fires
    the whole mesh QLC+ already built.

  * Detect every routine that partially or inconsistently writes a channel
    whose QLC+ *Group* is Intensity (not just anything a Level slider
    happens to control -- see below) that a Level slider also controls.
    Intensity-group channels are HTP (highest-takes-precedence) in the QLC+
    engine (engine/src/fadechannel.cpp: HTP is only ever set when
    `channel->group() == QLCChannel::Intensity`), so a routine that only
    writes SOME of a slider's channels, or writes them on some chaser steps
    and not others, gets erased by a high fader (Dim Chase, Spotlight, the
    Dark Moves chasers). Every hit must be classified by hand in
    ui_layout.js (dimPark or dimParkExempt), or --check fails -- that is
    what lets a new dim-touching routine added in QLC+ be caught instead of
    silently inheriting a guess.

    Only Intensity-group channels get this treatment, deliberately. The pin
    fixture's dimmer-shaped channel ("Total function control", DMX 9-134 =
    White Dimmer per its own capability table) is formally Group="Effect",
    not Intensity -- confirmed against qlcplus/resources/fixtures/UKing/
    UKing-ZQB93-Pinspot-RGBW.qxf. That means Pin Dim is LTP, an
    order-dependent last-write-wins channel, NOT HTP -- a fundamentally
    different (and, per qmlui/virtualconsole/vcslider.cpp:1357, slider-
    specific-AutoRemove-flavoured) mechanism than the clean HTP case this
    generator can confidently reason about. So it is deliberately NOT
    included in the conflict scan -- see despacio/README.md's note on
    "Pin Breathe" for the live-testing caveat that follows from this.

  * Matching between ui_layout.js and the live workspace is by CAPTION, not
    widget ID, so re-adding/reordering a widget in QLC+ (which reassigns its
    ID) doesn't require editing ui_layout.js.

  * Flag every widget that places a "pose" (writes a Position-group channel,
    i.e. Pan/Tilt) or a "color look" (writes a Color-group channel) --
    app.js's "a layer is running with no pose under it" / "MH Dim is up but
    no color look is active" show banners need this, and it used to be a
    hand-kept caption list in app.js that nothing validated against the live
    workspace (and had gone stale -- see resolve_movement_and_color()'s
    docstring). Reuses the same Chaser/Collection -> Scene flattening and
    FixtureVal parsing the HTP conflict scan above already does, just
    checked against Position/Color instead of Intensity, and covers
    CueList widgets (e.g. the Night cue list) the same way
    resolve_aim_warnings() does -- something the old caption list, being
    Button-only, never could.

Run:
    python gen_webui_config.py            # writes ui_config.js
    python gen_webui_config.py --check    # validates only, no write; used by preflight.py
"""
import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent   # events/despacio/
REPO = BASE.parent.parent             # lights/
WEBUI = Path(__file__).parent         # events/despacio/webui/
QXW = BASE / "despacio.qxw"
LAYOUT_JS = WEBUI / "ui_layout.js"
OUT_JS = WEBUI / "ui_config.js"
CONFIG_JSON = BASE / "despacio_config.json"
AIM_REPORT = BASE / "aim_report.json"
NSW = "{http://www.qlcplus.org/Workspace}"
NSF = "{http://www.qlcplus.org/FixtureDefinition}"

BLACKOUT_FUNCTION_ID = 4294967295   # QLC+'s "no function" sentinel
INDEFINITE_HOLD = 4294967294        # QLC+'s "hold forever" sentinel

# Fixture .qxf search roots, in priority order: the repo's shared fixture
# library, then anything the user has installed, then the vendored QLC+
# source tree (the stock library QLC+ actually compiles its bundled
# fixtures from -- see module docstring re: the pinspot profile).
#
# The vendored tree is gitignored, so it must never be the only source for a
# profile the show depends on -- shared/fixtures/ is the one that survives a
# fresh clone. The pinspot lived only in the vendored tree until 2026-08-06.
QXF_SEARCH_ROOTS = [
    REPO / "shared" / "fixtures",
    Path.home() / "QLC+" / "Fixtures",
    REPO / "qlcplus" / "resources" / "fixtures",
]

# QLCChannel presets this show's fixtures actually use, mapped to the group
# QLCChannel::setPreset() assigns them (engine/src/qlcchannel.cpp -- `Group
# grp = Intensity` is the default and most presets never override it; a few,
# like the Position* ones below, do). Deliberately NOT a full reimplementation
# of that switch -- only presets seen in this show are covered; anything else
# is treated as unknown (see resolve_channel_groups) rather than guessed.
PRESET_GROUPS = {
    "IntensityDimmer": "Intensity",
    "IntensityDimmerFine": "Intensity",
    "IntensityRed": "Intensity", "IntensityRedFine": "Intensity",
    "IntensityGreen": "Intensity", "IntensityGreenFine": "Intensity",
    "IntensityBlue": "Intensity", "IntensityBlueFine": "Intensity",
    "IntensityWhite": "Intensity", "IntensityWhiteFine": "Intensity",
    "IntensityMasterDimmer": "Intensity", "IntensityMasterDimmerFine": "Intensity",
    "PositionPan": "Position", "PositionPanFine": "Position",
    "PositionTilt": "Position", "PositionTiltFine": "Position",
}


def qn(tag):
    return f"{NSW}{tag}"


def qf(tag):
    return f"{NSF}{tag}"


def parse_channel_values(fv_text):
    """'7,255,4,0' -> {7: 255, 4: 0}"""
    if not fv_text:
        return {}
    vals = [int(x) for x in fv_text.split(",")]
    if len(vals) % 2:
        raise ValueError(f"odd channel,value list: {fv_text!r}")
    return dict(zip(vals[::2], vals[1::2]))


def find_qxf(manufacturer, model):
    """Best-effort search for a fixture's .qxf across QXF_SEARCH_ROOTS,
    matching on the <Manufacturer>/<Model> text inside the file (not the
    filename, which doesn't always match). Returns a path or None."""
    for root in QXF_SEARCH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.qxf"):
            try:
                fx_root = ET.parse(path).getroot()  # root IS the <FixtureDefinition> element
            except ET.ParseError:
                continue
            man = fx_root.find(qf("Manufacturer"))
            mod = fx_root.find(qf("Model"))
            if man is not None and mod is not None and man.text == manufacturer and mod.text == model:
                return path
    return None


def channel_group(channel_el):
    """A qxf <Channel>'s effective QLCChannel group: explicit <Group> wins,
    else a known preset's default group, else unknown (None) -- see
    PRESET_GROUPS for why this isn't a full preset table."""
    group_el = channel_el.find(qf("Group"))
    if group_el is not None and group_el.text:
        return group_el.text
    preset = channel_el.get("Preset")
    if preset in PRESET_GROUPS:
        return PRESET_GROUPS[preset]
    return None


def resolve_channel_groups(ws):
    """{fixture_id: {channel_index: group_string}} for every channel of every
    fixture in the workspace, in its patched Mode -- the general form of what
    used to be resolve_intensity_channels()'s Intensity-only lookup. Group
    resolution itself (explicit <Group> tag, else a known Preset's default
    group -- see channel_group()/PRESET_GROUPS) doesn't care which group it
    finds, so this single walk of the Fixture/.qxf/Mode tree serves every
    caller that needs a channel's group, not just the HTP dim-conflict scan:
    resolve_dim_conflicts() below narrows this to "Intensity", and
    resolve_movement_and_color() narrows it to "Position"/"Color". Fixtures
    whose .qxf can't be found, or whose active Mode isn't in it, are skipped
    (printed as a warning) and excluded from EVERY caller consistently --
    never guessed by any of them individually."""
    result = {}
    qxf_cache = {}  # (manufacturer, model) -> (parsed root, {channel name: group}) or None
    for f in ws.eng.findall(qn("Fixture")):
        fid = int(f.find(qn("ID")).text)
        manufacturer = f.find(qn("Manufacturer")).text
        model = f.find(qn("Model")).text
        mode = f.find(qn("Mode")).text
        key = (manufacturer, model)

        if key not in qxf_cache:
            path = find_qxf(manufacturer, model)
            if path is None:
                print(f"WARNING: no .qxf found for {manufacturer} {model!r} (fixture {fid}) -- "
                      f"its channels are excluded from HTP/dimmer-conflict and pose/color analysis")
                qxf_cache[key] = None
            else:
                fx_root = ET.parse(path).getroot()
                groups_by_channel_name = {
                    c.get("Name"): channel_group(c) for c in fx_root.findall(qf("Channel"))
                }
                qxf_cache[key] = (fx_root, groups_by_channel_name)

        cached = qxf_cache[key]
        if cached is None:
            continue
        fx_root, groups_by_channel_name = cached

        mode_el = next((m for m in fx_root.findall(qf("Mode")) if m.get("Name") == mode), None)
        if mode_el is None:
            print(f"WARNING: mode {mode!r} not found in .qxf for {manufacturer} {model!r} "
                  f"(fixture {fid}) -- excluded from HTP/dimmer-conflict and pose/color analysis")
            continue

        groups = {}
        for idx, ch_ref in enumerate(mode_el.findall(qf("Channel"))):
            g = groups_by_channel_name.get(ch_ref.text)
            if g:
                groups[idx] = g
        result[fid] = groups
    return result


def resolve_intensity_channels(channel_groups):
    """{fixture_id: {channel_index, ...}} of channels whose QLCChannel group
    is Intensity -- i.e. the only channels QLC+'s engine ever treats as HTP.
    Thin filter over resolve_channel_groups()'s full per-channel mapping."""
    return {
        fid: {idx for idx, g in groups.items() if g == "Intensity"}
        for fid, groups in channel_groups.items()
    }


class Workspace:
    def __init__(self, path):
        self.root = ET.parse(path).getroot()
        self.eng = self.root.find(qn("Engine"))
        self.vc = self.root.find(qn("VirtualConsole"))
        self.functions = {int(fn.get("ID")): fn for fn in self.eng.findall(qn("Function"))}

    def function_name(self, fid):
        fn = self.functions.get(fid)
        return fn.get("Name") if fn is not None else None

    def scene_steps(self, fid, _seen=None):
        """Flatten a function id to the Scene *elements* it can ever run,
        recursing through Collection/Chaser <Step> refs. EFX and other
        algorithmic types contribute no FixtureVal writes, so they yield
        nothing -- that's correct: Drift/Counter-Orbit don't touch Dimmer."""
        if _seen is None:
            _seen = set()
        if fid in _seen or fid not in self.functions:
            return []
        _seen.add(fid)
        fn = self.functions[fid]
        ftype = fn.get("Type")
        if ftype == "Scene":
            return [fn]
        if ftype in ("Collection", "Chaser"):
            out = []
            for st in fn.findall(qn("Step")):
                out.extend(self.scene_steps(int(st.text), _seen))
            return out
        return []


# ---------------------------------------------------------------------------
# Virtual console walk
# ---------------------------------------------------------------------------

def parse_button(el, ws):
    fn_el = el.find(qn("Function"))
    fid = int(fn_el.get("ID")) if fn_el is not None else None
    action_el = el.find(qn("Action"))
    return {
        "kind": "button",
        "id": int(el.get("ID")),
        "caption": el.get("Caption"),
        "functionId": fid if fid != BLACKOUT_FUNCTION_ID else None,
        "functionName": ws.function_name(fid) if fid not in (None, BLACKOUT_FUNCTION_ID) else None,
        "functionType": (ws.functions[fid].get("Type") if fid in ws.functions else None),
        "action": action_el.text if action_el is not None else "Toggle",
        "hasInput": el.find(qn("Input")) is not None,
    }


def parse_slider(el, ws):
    mode_el = el.find(qn("SliderMode"))
    level_el = el.find(qn("Level"))
    adjust_el = el.find(qn("Adjust"))
    channels = []
    if level_el is not None:
        for ch in level_el.findall(qn("Channel")):
            channels.append([int(ch.get("Fixture")), int(ch.text)])
    return {
        "kind": "slider",
        "id": int(el.get("ID")),
        "caption": el.get("Caption"),
        "mode": mode_el.text if mode_el is not None else None,
        "displayStyle": mode_el.get("ValueDisplayStyle") if mode_el is not None else None,
        "low": int(level_el.get("LowLimit")) if level_el is not None else 0,
        "high": int(level_el.get("HighLimit")) if level_el is not None else 255,
        "value": int(level_el.get("Value")) if level_el is not None else 0,
        "channels": channels,
        "adjustFunctionId": int(adjust_el.get("Function")) if adjust_el is not None else None,
    }


def parse_cuelist(el, ws):
    chaser_el = el.find(qn("Chaser"))
    chaser_id = int(chaser_el.text) if chaser_el is not None and chaser_el.text else None
    sliders_mode_el = el.find(qn("SlidersMode"))
    steps = []
    if chaser_id is not None and chaser_id in ws.functions:
        for st in ws.functions[chaser_id].findall(qn("Step")):
            step_fid = int(st.text)
            hold = int(st.get("Hold", 0))
            steps.append({
                "functionId": step_fid,
                "name": ws.function_name(step_fid),
                "holdMs": None if hold >= INDEFINITE_HOLD else hold,
            })
    return {
        "kind": "cuelist",
        "id": int(el.get("ID")),
        "caption": el.get("Caption"),
        "chaserId": chaser_id,
        "chaserName": ws.function_name(chaser_id) if chaser_id is not None else None,
        "slidersMode": sliders_mode_el.text if sliders_mode_el is not None else None,
        "steps": steps,
    }


def parse_speeddial(el, ws):
    time_el = el.find(qn("Time"))
    abs_el = el.find(qn("AbsoluteValue"))
    fn_ids = [int(f.text) for f in el.findall(qn("Function"))]
    return {
        "kind": "speeddial",
        "id": int(el.get("ID")),
        "caption": el.get("Caption"),
        "timeMs": int(time_el.text) if time_el is not None else None,
        "minMs": int(abs_el.get("Minimum")) if abs_el is not None else None,
        "maxMs": int(abs_el.get("Maximum")) if abs_el is not None else None,
        "attachedFunctionIds": fn_ids,
    }


PARSERS = {
    "Button": parse_button,
    "Slider": parse_slider,
    "CueList": parse_cuelist,
    "SpeedDial": parse_speeddial,
}


def walk_widgets(ws):
    """Depth-first walk of the VirtualConsole tree. Returns the flat list of
    real (non-"~") widgets, in document order."""
    widgets = []

    def visit(el):
        for child in el:
            tag = child.tag.replace(NSW, "")
            if tag in PARSERS:
                caption = child.get("Caption") or ""
                if caption.startswith("~"):
                    continue  # hidden SoloFrame mirror -- never expose to the UI
                widgets.append(PARSERS[tag](child, ws))
            if tag in ("Frame", "SoloFrame"):
                visit(child)

    visit(ws.vc)
    return widgets


# ---------------------------------------------------------------------------
# Dimmer (HTP) conflict analysis
# ---------------------------------------------------------------------------

def find_dim_conflicts(ws, widgets, intensity_channels):
    """For every real button and every Level slider whose channels are ALL
    QLC+ Intensity-group (i.e. genuinely HTP), detect whether the button's
    function writes that channel set only partially or inconsistently across
    steps -- the signature of "gets erased by an HTP fader". See module
    docstring for what this does and doesn't decide on its own, and why
    non-Intensity channels (e.g. the pin dimmer) are excluded entirely."""
    level_sliders = []
    for w in widgets:
        if w["kind"] != "slider" or w["mode"] != "Level" or not w["channels"]:
            continue
        pairs = [tuple(c) for c in w["channels"]]
        is_intensity = [p[1] in intensity_channels.get(p[0], set()) for p in pairs]
        if all(is_intensity):
            level_sliders.append(w)
        elif any(is_intensity):
            print(f"WARNING: slider {w['caption']!r} mixes Intensity and non-Intensity channels "
                  f"-- excluded from dim-conflict analysis (unexpected; worth a look)")
        # else: none of this slider's channels are Intensity-group (e.g. Pin Dim) -- silently
        # excluded, that's the expected/correct case, not a warning-worthy one.
    conflicts = []

    for slider in level_sliders:
        channel_set = {tuple(c) for c in slider["channels"]}
        for btn in (w for w in widgets if w["kind"] == "button"):
            if btn["functionId"] is None:
                continue
            steps = ws.scene_steps(btn["functionId"])
            if not steps:
                continue

            step_subsets = []
            all_written = set()
            for step in steps:
                written = set()
                for fv in step.findall(qn("FixtureVal")):
                    fixture_id = int(fv.get("ID"))
                    for ch, _val in parse_channel_values(fv.text).items():
                        pair = (fixture_id, ch)
                        if pair in channel_set:
                            written.add(pair)
                step_subsets.append(written)
                all_written |= written

            if not all_written:
                continue  # this routine never touches the slider's channels at all
            consistent = all(s == all_written for s in step_subsets)
            if all_written != channel_set or not consistent:
                conflicts.append({
                    "buttonId": btn["id"],
                    "buttonCaption": btn["caption"],
                    "sliderId": slider["id"],
                    "sliderCaption": slider["caption"],
                    "fixturesWritten": sorted(f for f, _ in all_written),
                    "fixturesInSlider": sorted({f for f, _ in channel_set}),
                })
    return conflicts


# ---------------------------------------------------------------------------
# Pose / color detection (structural replacement for app.js's hand-kept
# POSE_CAPTIONS / COLOR_CAPTIONS caption lists)
# ---------------------------------------------------------------------------

def resolve_movement_and_color(ws, widgets, channel_groups):
    """(pose_captions, color_captions) -- sorted caption lists for the
    despacio show banners (app.js's "a layer is running with no pose under
    it" / "MH Dim is up but no color look is active" checks).

    A widget counts as a POSE if any Scene step any of its functions can ever
    run writes a Position-group channel (Pan/Tilt) -- i.e. it places the
    heads somewhere, the thing a layer (Drift/Counter-Orbit) needs under it
    to not immediately integrate to a rail. It counts as a COLOR look if any
    step writes a Color-group channel (this show's moving heads expose their
    color as a single Color-Wheel-preset channel, not RGB intensities --
    see MingJie-MJ-OS-018-60W-Beam.qxf).

    This replaces app.js's LAYER_CAPTIONS-adjacent POSE_CAPTIONS/
    COLOR_CAPTIONS arrays, which were hand-kept caption lists nothing ever
    validated against the live workspace (a QLC+ rename broke them silently)
    and which only ever looked at Button widgets -- so a pose or color look
    driven by the Night CueList's chaser was invisible to them. Using the
    same Chaser/Collection -> Scene flattening (Workspace.scene_steps()) and
    FixtureVal parsing find_dim_conflicts() already does -- just checked
    against Position/Color instead of Intensity -- covers CueList widgets
    for free (see the functionId-vs-chaserId branch below, matching
    resolve_aim_warnings()'s widget_scene_ids pattern), and the caption list
    lint below (see lint()) means a future rename fails preflight instead of
    silently going stale.

    Only Button and CueList widgets carry a function that can reach a
    Position/Color-writing Scene (Sliders/SpeedDials in this show don't),
    same restriction as resolve_aim_warnings()."""
    pose_captions = set()
    color_captions = set()
    for w in widgets:
        if w["kind"] == "button":
            fid = w.get("functionId")
        elif w["kind"] == "cuelist":
            fid = w.get("chaserId")
        else:
            continue
        if fid is None:
            continue

        writes_position = False
        writes_color = False
        for step in ws.scene_steps(fid):
            for fv in step.findall(qn("FixtureVal")):
                fixture_id = int(fv.get("ID"))
                groups = channel_groups.get(fixture_id, {})
                for ch in parse_channel_values(fv.text):
                    g = groups.get(ch)
                    if g == "Position":
                        writes_position = True
                    elif g == "Colour":
                        writes_color = True
            if writes_position and writes_color:
                break  # nothing more this widget could tell us

        if writes_position:
            pose_captions.add(w["caption"])
        if writes_color:
            color_captions.add(w["caption"])

    return sorted(pose_captions), sorted(color_captions)


# ---------------------------------------------------------------------------
# Pose reachability warnings (aim_calc.py's aim_report.json)
# ---------------------------------------------------------------------------

def resolve_aim_warnings(ws, widgets, aim_report):
    """Turn aim_calc.py's aim_report.json (a list of {head, pose, functionIds,
    severity, ...} -- see aim_calc.py's unreachable_poses()) into
    {caption: {poses, maxErrorDeg, severity}}, so the phone UI can mark the
    actual on-screen buttons/cuelists a clamped pose feeds, instead of a bare
    function-ID list nobody driving the show can act on.

    Matching is via Workspace.scene_steps(), the same Chaser/Collection ->
    Scene flattening find_dim_conflicts() already relies on: a widget's
    "reach" is every Scene ID its own function can ever trigger. Only Button
    and CueList widgets carry a function that can reach a pose-driven Scene
    (Sliders/SpeedDials in this show don't move Pan/Tilt), so those are the
    only kinds considered here.

    Returns [] if `aim_report` is None/empty -- the caller is responsible for
    deciding when that's worth a NOTE (missing file, stale mode, etc.); this
    function only does the resolution, no I/O of its own."""
    if not aim_report:
        return []
    unreachable = aim_report.get("unreachable", [])
    if not unreachable:
        return []

    widget_scene_ids = {}
    for w in widgets:
        if w["kind"] == "button":
            fid = w.get("functionId")
        elif w["kind"] == "cuelist":
            fid = w.get("chaserId")
        else:
            continue
        if fid is None:
            continue
        ids = {int(s.get("ID")) for s in ws.scene_steps(fid)}
        if ids:
            widget_scene_ids[w["caption"]] = ids

    by_caption = {}
    for entry in unreachable:
        fn_ids = set(entry.get("functionIds", []))
        if not fn_ids:
            continue
        err = max(entry.get("errorBearingDeg", 0), entry.get("errorElevDeg", 0))
        pose_label = f"head{entry['head'] + 1}:{entry['pose']}"
        for caption, ids in widget_scene_ids.items():
            if not (ids & fn_ids):
                continue
            rec = by_caption.setdefault(
                caption, {"poses": set(), "maxErrorDeg": 0.0, "severity": "minor"})
            rec["poses"].add(pose_label)
            rec["maxErrorDeg"] = max(rec["maxErrorDeg"], err)
            if entry.get("severity") == "significant":
                rec["severity"] = "significant"

    return [
        {"caption": caption, "poses": sorted(rec["poses"]),
         "maxErrorDeg": round(rec["maxErrorDeg"], 1), "severity": rec["severity"]}
        for caption, rec in sorted(by_caption.items())
    ]


# ---------------------------------------------------------------------------
# ui_layout.js linting (regex scrape -- no JS parser needed, see module docstring)
# ---------------------------------------------------------------------------

_QUOTED = re.compile(r"""['"]([^'"]+)['"]""")


def scrape_layout(layout_text):
    """Best-effort scrape of ui_layout.js. Returns:
      item_captions   -- every caption referenced in a tab section's `items:` array
      dim_park        -- captions used as keys in `dimPark: { ... }`
      dim_park_exempt -- captions referenced in `dimParkExempt: [ ... ]`
      layer_captions  -- captions referenced in `layerCaptions: [ ... ]`
    Not a real JS parser -- ui_layout.js must keep `items`, `dimPark`,
    `dimParkExempt`, and `layerCaptions` as simple, single-line-friendly
    literal blocks for this to see them correctly.
    """
    item_captions = set()
    for m in re.finditer(r"items\s*:\s*\[([^\]]*)\]", layout_text):
        item_captions.update(_QUOTED.findall(m.group(1)))

    dim_park = set()
    m = re.search(r"dimPark\s*:\s*\{([^}]*)\}", layout_text)
    if m:
        # keys are the quoted strings immediately followed by a colon
        dim_park.update(re.findall(r"""['"]([^'"]+)['"]\s*:""", m.group(1)))

    dim_park_exempt = set()
    m = re.search(r"dimParkExempt\s*:\s*\[([^\]]*)\]", layout_text)
    if m:
        dim_park_exempt.update(_QUOTED.findall(m.group(1)))

    layer_captions = set()
    m = re.search(r"layerCaptions\s*:\s*\[([^\]]*)\]", layout_text)
    if m:
        layer_captions.update(_QUOTED.findall(m.group(1)))

    return item_captions, dim_park, dim_park_exempt, layer_captions


def find_duplicate_placements(layout_text):
    """Every caption that appears in more than one `items: [...]` block
    (header.items counts too -- it uses the same `items:` key as every tab
    section, so one regex sweep already covers both). See lint()'s point 5
    for why a repeat here is a live bug, not just untidy layout."""
    all_captions = []
    for m in re.finditer(r"items\s*:\s*\[([^\]]*)\]", layout_text):
        all_captions.extend(_QUOTED.findall(m.group(1)))
    counts = {}
    for cap in all_captions:
        counts[cap] = counts.get(cap, 0) + 1
    return sorted(cap for cap, n in counts.items() if n > 1)


def lint(widgets, conflicts, layout_text):
    """Returns (errors, info_lines)."""
    errors = []
    info = []

    real_captions = {w["caption"] for w in widgets}
    item_captions, dim_park, dim_park_exempt, layer_captions = scrape_layout(layout_text)

    # 1. Every caption ui_layout.js places must be a real widget -- catches
    #    typos and QLC+ renames before they silently drop a control.
    #    layerCaptions is included here too: EFX-type functions (Drift/
    #    Counter-Orbit) write no Scene FixtureVal at all, so which widgets are
    #    "layers, not poses" isn't structurally derivable from the qxw the way
    #    dim-park or pose/color detection is (see resolve_movement_and_color())
    #    -- it stays a hand-kept list, but linted here so a rename fails
    #    preflight instead of silently going stale.
    for cap in sorted(item_captions | dim_park | dim_park_exempt | layer_captions):
        if cap not in real_captions:
            errors.append(f"ui_layout.js references {cap!r} but no real widget has that caption "
                           f"(renamed or removed in despacio.qxw?)")

    # 2. Every real widget not placed anywhere is only informational -- the
    #    phone UI renders it under "Unsorted" at runtime rather than hiding it.
    unplaced = sorted(real_captions - item_captions)
    if unplaced:
        info.append(f"{len(unplaced)} real widget(s) not placed in ui_layout.js "
                     f"(will render under Unsorted): {unplaced}")

    # 3. The safety gate: every detected dim conflict must be explicitly
    #    classified as either a park target or an exemption. No silent default.
    classified = dim_park | dim_park_exempt
    unclassified = sorted({c["buttonCaption"] for c in conflicts} - classified)
    if unclassified:
        errors.append(
            "The following routines write their slider's HTP channel only partially or "
            "inconsistently and are NOT classified in ui_layout.js's dimPark or "
            f"dimParkExempt -- classify each one: {unclassified}"
        )

    # 4. Two real widgets sharing a caption breaks the whole by-caption-match
    #    design point-blank: app.js's byId/byCaption maps (and this script's
    #    own widget_scene_ids/aimWarningsByCaption keying) are keyed by
    #    caption, so the second widget silently overwrites the first with no
    #    error -- one of them becomes permanently unreachable from the phone.
    caption_ids = {}
    for w in widgets:
        caption_ids.setdefault(w["caption"], []).append(w["id"])
    dupe_captions = {cap: ids for cap, ids in caption_ids.items() if len(ids) > 1}
    if dupe_captions:
        errors.append(
            "The following captions are shared by more than one real widget in "
            f"despacio.qxw (widget IDs in parens): {dupe_captions} -- give each a unique "
            "caption; ui_layout.js and this generator both match by caption, not ID."
        )

    # 5. A caption placed in more than one `items:` array (header AND a tab
    #    section, or two sections) is a live bug, not just a lint nag: the
    #    phone's widgetEl() resolves a widget's DOM node with a plain
    #    querySelector (first match wins), so every placement after the first
    #    renders a control that's permanently unpainted -- taps still fire
    #    (the click handler is real), but its visual state never updates.
    dupe_placements = find_duplicate_placements(layout_text)
    if dupe_placements:
        errors.append(
            f"The following captions are placed in more than one items: array: "
            f"{dupe_placements} -- app.js's widgetEl() only ever paints the FIRST "
            "matching DOM node, so every later placement of the same caption is a "
            "dead, unpainted duplicate. Place each caption exactly once."
        )

    return errors, info


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------

def build_config(ws, widgets, conflicts, aim_warnings, pose_captions, color_captions):
    qxw_hash = hashlib.sha256(QXW.read_bytes()).hexdigest()
    return {
        "_generated": {
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "qxwSha256": qxw_hash,
            "qxwPath": "despacio.qxw",
        },
        "widgets": widgets,
        "dimConflicts": conflicts,
        "aimWarnings": aim_warnings,
        "poseCaptions": pose_captions,
        "colorCaptions": color_captions,
    }


def write_config(config):
    body = json.dumps(config, indent=2)
    OUT_JS.write_text(
        "// GENERATED by gen_webui_config.py -- do not hand-edit, it will be overwritten.\n"
        f"window.VC = {body};\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                         help="Validate only; don't write ui_config.js. Exit 1 on any problem.")
    args = parser.parse_args()

    if not QXW.exists():
        print(f"ERROR: {QXW} not found", file=sys.stderr)
        return 1

    ws = Workspace(QXW)
    widgets = walk_widgets(ws)
    channel_groups = resolve_channel_groups(ws)
    intensity_channels = resolve_intensity_channels(channel_groups)
    conflicts = find_dim_conflicts(ws, widgets, intensity_channels)

    by_kind = {}
    for w in widgets:
        by_kind[w["kind"]] = by_kind.get(w["kind"], 0) + 1
    print(f"Parsed {len(widgets)} real widgets: {by_kind}")
    print(f"Dimmer HTP conflicts detected: {len(conflicts)}")
    for c in conflicts:
        print(f"  - {c['buttonCaption']!r} (fn writes fixtures {c['fixturesWritten']} of "
              f"{c['fixturesInSlider']}) vs slider {c['sliderCaption']!r}")

    # Structural replacement for app.js's hand-kept POSE_CAPTIONS/
    # COLOR_CAPTIONS -- see resolve_movement_and_color()'s docstring.
    pose_captions, color_captions = resolve_movement_and_color(ws, widgets, channel_groups)
    print(f"Pose-bearing widgets: {len(pose_captions)}  Color-look widgets: {len(color_captions)}")

    # aim_calc.py's pose-reachability report -- advisory only, never fails
    # --check (see resolve_aim_warnings()'s docstring): a missing or stale
    # report just means the phone UI won't show a warning marker yet, not
    # that the workspace itself is wrong.
    aim_report = None
    if not AIM_REPORT.exists():
        print(f"NOTE: {AIM_REPORT} not found -- aimWarnings will be empty "
              f"(run aim_calc.py to generate it)")
    else:
        try:
            aim_report = json.loads(AIM_REPORT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"NOTE: {AIM_REPORT} is not valid JSON -- aimWarnings will be empty")
        else:
            if CONFIG_JSON.exists():
                active_mode = json.loads(CONFIG_JSON.read_text(encoding="utf-8")).get("mount_mode")
                if aim_report.get("mode") != active_mode:
                    print(f"NOTE: {AIM_REPORT} is for mode {aim_report.get('mode')!r}, active "
                          f"mode is {active_mode!r} -- rerun aim_calc.py; aimWarnings may be stale")
    aim_warnings = resolve_aim_warnings(ws, widgets, aim_report)
    if aim_warnings:
        print(f"Pose reachability warnings: {len(aim_warnings)} widget(s) affected")
        for w in aim_warnings:
            print(f"  - {w['caption']!r} {w['severity'].upper()} "
                  f"(max {w['maxErrorDeg']}deg off): {w['poses']}")

    errors, info = [], []
    if LAYOUT_JS.exists():
        errors, info = lint(widgets, conflicts, LAYOUT_JS.read_text(encoding="utf-8"))
    else:
        errors.append(f"{LAYOUT_JS} not found -- cannot lint placement/dim-park classification")

    for line in info:
        print(f"NOTE: {line}")

    if errors:
        print("\nERRORS:")
        for e in errors:
            print(" -", e)
        return 1

    if args.check:
        print("\n--check OK (no file written)")
        return 0

    config = build_config(ws, widgets, conflicts, aim_warnings, pose_captions, color_captions)
    write_config(config)
    print(f"\nWrote {OUT_JS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
