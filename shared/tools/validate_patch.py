"""
Reads patch_sheet.csv and validates the DMX patch.

Checks:
  - No two fixtures share a DMX channel (overlap detection)
  - All fixtures fit within channels 1-512
  - No fixture has zero channels
  - Reports gaps between fixture blocks (info only)
  - Warns about missing GDTF profiles

Usage:
  python shared/tools/validate_patch.py                 # every event
  python shared/tools/validate_patch.py --event despacio
  python shared/tools/validate_patch.py --csv path/to/other.csv
"""

import csv
import sys
import argparse
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent.parent   # lights/
EVENTS_DIR = REPO / "events"


def event_patch_sheets() -> dict[str, Path]:
    """Every events/<name>/patch_sheet.csv, keyed by event name."""
    if not EVENTS_DIR.is_dir():
        return {}
    return {
        d.name: d / "patch_sheet.csv"
        for d in sorted(EVENTS_DIR.iterdir())
        if (d / "patch_sheet.csv").is_file()
    }


def load_patch(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def validate(rows: list[dict]) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    # Occupancy and gaps are tracked PER UNIVERSE. The same DMX channel in two
    # different Art-Net universes is not a conflict — a single flat channel map
    # would report a mixed-universe rig (e.g. a club house rig alongside ours)
    # as one giant overlap.
    occupied: dict[tuple[int, int], str] = {}          # (universe, channel) -> name
    ranges_by_uni: dict[int, list[tuple[int, int, str]]] = {}   # uni -> [(start, end, name)]

    for line_no, row in enumerate(rows, start=2):   # +2: header occupies line 1
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"row {line_no}: missing 'name' column or empty name")
            name = f"<unnamed row {line_no}>"

        try:
            start = int(row["dmx_start"])
            count = int(row["channel_count"])
            artnet_uni = int(row["artnet_universe"])
        except (ValueError, KeyError) as e:
            errors.append(f"{name}: bad numeric field — {e}")
            continue

        if count <= 0:
            errors.append(f"{name}: channel_count must be > 0")
            continue

        end = start + count - 1

        if start < 1 or end > 512:
            errors.append(
                f"{name}: channels {start}–{end} out of range 1–512 "
                f"(universe {artnet_uni})"
            )

        # One error per conflicting fixture, not one per overlapping channel —
        # a 30-channel clash used to emit 30 near-identical lines.
        clashes = sorted({
            occupied[(artnet_uni, ch)]
            for ch in range(start, end + 1)
            if (artnet_uni, ch) in occupied
        })
        for other in clashes:
            errors.append(
                f"OVERLAP: {name} (universe {artnet_uni}, ch {start}–{end}) "
                f"conflicts with {other}"
            )
        for ch in range(start, end + 1):
            occupied.setdefault((artnet_uni, ch), name)

        if not row.get("gdtf_profile", "").strip():
            warnings.append(f"{name}: no GDTF profile set in patch sheet")

        ranges_by_uni.setdefault(artnet_uni, []).append((start, end, name))

    # Gap detection — within each universe, by start address
    gap_pairs: list[tuple[int, int, int, str, str]] = []
    for uni in sorted(ranges_by_uni):
        fixture_ranges = sorted(ranges_by_uni[uni])
        for i in range(len(fixture_ranges) - 1):
            _, end_a, name_a = fixture_ranges[i]
            start_b, _, name_b = fixture_ranges[i + 1]
            gap_pairs.append((uni, end_a, start_b, name_a, name_b))

    for uni, end_a, start_b, name_a, name_b in gap_pairs:
        gap = start_b - end_a - 1
        if gap > 0:
            info.append(
                f"Universe {uni}: gap of {gap} unused channel(s) between "
                f"{name_a} (ends {end_a}) and {name_b} (starts {start_b})"
            )

    return errors, warnings, info


def print_summary(rows: list[dict], errors, warnings, info):
    print("=" * 60)
    print("PATCH SHEET SUMMARY")
    print("=" * 60)

    universes: dict[int, list[dict]] = {}
    for row in rows:
        try:
            uni = int(row["artnet_universe"])
        except (ValueError, KeyError):
            uni = -1
        universes.setdefault(uni, []).append(row)

    for uni in sorted(universes):
        print(f"\nArt-Net Universe {uni}:")
        uni_rows = sorted(universes[uni], key=lambda r: int(r.get("dmx_start", 0)))
        total_channels = 0
        for row in uni_rows:
            try:
                start = int(row["dmx_start"])
                count = int(row["channel_count"])
            except (ValueError, KeyError):
                continue
            end = start + count - 1
            gdtf = row.get("gdtf_profile", "").strip() or "(no GDTF)"
            name = (row.get("name") or "").strip() or "(unnamed)"
            print(
                f"  ch {start:>3}–{end:<3}  {count:>2}ch  "
                f"{name:<35} {gdtf}"
            )
            total_channels += count
        print(f"  Total assigned channels: {total_channels}")

    print()
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  [ERR]  {e}")
    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  [WARN] {w}")
    if info:
        print(f"INFO ({len(info)}):")
        for i in info:
            print(f"  [INFO] {i}")

    print()
    if errors:
        print(f"Result: FAIL — {len(errors)} error(s)")
    else:
        print(f"Result: OK — {len(rows)} fixtures, {len(warnings)} warning(s)")
    print("=" * 60)


def validate_one(label: str, path: Path) -> bool:
    """Validate a single patch sheet. Returns True if it has no errors."""
    print(f"\n=== {label} ({path}) ===")
    rows = load_patch(path)
    errors, warnings, info = validate(rows)
    print_summary(rows, errors, warnings, info)
    return not errors


def main():
    parser = argparse.ArgumentParser(description="Validate DMX patch sheet(s)")
    parser.add_argument("--csv", type=Path,
                        help="Validate one specific patch sheet by path")
    parser.add_argument("--event",
                        help="Validate one event by name (a directory under events/)")
    args = parser.parse_args()

    if args.csv and args.event:
        print("Error: pass --csv or --event, not both", file=sys.stderr)
        sys.exit(2)

    if args.csv:
        targets = {args.csv.stem: args.csv}
    else:
        sheets = event_patch_sheets()
        if args.event:
            if args.event not in sheets:
                known = ", ".join(sheets) or "none found"
                print(f"Error: unknown event {args.event!r} (known: {known})",
                      file=sys.stderr)
                sys.exit(2)
            targets = {args.event: sheets[args.event]}
        else:
            targets = sheets

    if not targets:
        print(f"Error: no patch sheets found under {EVENTS_DIR}", file=sys.stderr)
        sys.exit(1)

    ok = True
    for label, path in targets.items():
        if not path.exists():
            print(f"Error: patch sheet not found at {path}", file=sys.stderr)
            ok = False
            continue
        ok &= validate_one(label, path)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
