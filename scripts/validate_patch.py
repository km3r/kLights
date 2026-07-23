"""
Reads patch_sheet.csv and validates the DMX patch.

Checks:
  - No two fixtures share a DMX channel (overlap detection)
  - All fixtures fit within channels 1-512
  - No fixture has zero channels
  - Reports gaps between fixture blocks (info only)
  - Warns about missing GDTF profiles

Usage:
  python scripts/validate_patch.py
  python scripts/validate_patch.py --csv path/to/other.csv
"""

import csv
import sys
import argparse
from pathlib import Path


DEFAULT_CSV = Path(__file__).parent.parent / "patch_sheet.csv"


def load_patch(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def validate(rows: list[dict]) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    # Build channel occupancy map: dmx_channel -> fixture_name
    occupied: dict[int, str] = {}

    fixture_ranges: list[tuple[int, int, str]] = []  # (start, end, name)

    for row in rows:
        name = row["name"]
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

        for ch in range(start, end + 1):
            if ch in occupied:
                errors.append(
                    f"OVERLAP: {name} (ch {ch}) conflicts with {occupied[ch]}"
                )
            else:
                occupied[ch] = name

        if not row.get("gdtf_profile", "").strip():
            warnings.append(f"{name}: no GDTF profile set in patch sheet")

        fixture_ranges.append((start, end, name))

    # Gap detection — sort by start address
    fixture_ranges.sort()
    for i in range(len(fixture_ranges) - 1):
        _, end_a, name_a = fixture_ranges[i]
        start_b, _, name_b = fixture_ranges[i + 1]
        gap = start_b - end_a - 1
        if gap > 0:
            info.append(
                f"Gap: {gap} unused channel(s) between {name_a} "
                f"(ends {end_a}) and {name_b} (starts {start_b})"
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
            print(
                f"  ch {start:>3}–{end:<3}  {count:>2}ch  "
                f"{row['name']:<35} {gdtf}"
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


def main():
    parser = argparse.ArgumentParser(description="Validate DMX patch sheet")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"Error: patch sheet not found at {args.csv}", file=sys.stderr)
        sys.exit(1)

    rows = load_patch(args.csv)
    errors, warnings, info = validate(rows)
    print_summary(rows, errors, warnings, info)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
