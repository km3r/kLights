"""
Generate GDTF fixture profiles for the Cosmos rig directly from the QLC+
channel maps. A .gdtf file is just a ZIP archive containing a description.xml
(GDTF 1.2 schema). BlenderDMX parses these with pygdtf.

Run:
    python shared/tools/build_gdtf.py

Writes one .gdtf per entry in FIXTURES (currently 6) into shared/gdtf/.

CAVEAT: despite the intent, this script does NOT read the .qxf files. The
channel order and mode names in FIXTURES below are hand-transcribed from
them, so the two can drift silently -- if you change a fixture's mode or
channel order in shared/fixtures/, you must mirror it here by hand. Making
this read shared/fixtures/*.qxf directly is worth doing.
"""

import os
import zipfile
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))   # shared/tools/
SHARED = os.path.dirname(HERE)                      # shared/
OUT_DIR = os.path.join(SHARED, "gdtf")
FIXTURE_LIB = os.path.join(SHARED, "fixtures")      # source of truth for .qxf

# ── Attribute catalogue ───────────────────────────────────────────────────────
# name -> (Pretty, "FeatureGroup.Feature")
# Standard GDTF names are used where BlenderDMX renders them (Dimmer, ColorAdd_*,
# Shutter1Strobe, Pan, Tilt, Zoom). Everything else is a control attribute that
# exists so the DMX channel is addressable but isn't visually rendered.
ATTRS = {
    "Dimmer":         ("Dim",     "Dimmer.Dimmer"),
    "ColorAdd_R":     ("R",       "Color.RGB"),
    "ColorAdd_G":     ("G",       "Color.RGB"),
    "ColorAdd_B":     ("B",       "Color.RGB"),
    "ColorAdd_W":     ("W",       "Color.RGB"),
    "Shutter1":       ("Open",    "Beam.Beam"),
    "Shutter1Strobe": ("Strobe",  "Beam.Beam"),
    "Pan":            ("Pan",     "Position.PanTilt"),
    "Tilt":           ("Tilt",    "Position.PanTilt"),
    "Zoom":           ("Zoom",    "Beam.Beam"),
    # control / non-rendered
    "Control":        ("Ctrl",    "Control.Control"),
    "Effects":        ("FX",      "Control.Control"),
    "Function":       ("Func",    "Control.Control"),
    "ColorMacro":     ("Macro",   "Control.Control"),
    "Pattern":        ("Patt",    "Control.Control"),
    "Rotation":       ("Rot",     "Control.Control"),
    "LaserRed":       ("LasR",    "Control.Control"),
    "LaserGreen":     ("LasG",    "Control.Control"),
    "LaserStrobe":    ("LasStr",  "Control.Control"),
    "Marquee":        ("Marq",    "Control.Control"),
    "MarqueeSpeed":   ("MarqSpd", "Control.Control"),
}

FEATURE_GROUPS = [
    ("Dimmer",   "Dimmer",   ["Dimmer"]),
    ("Color",    "Color",    ["RGB"]),
    ("Position", "Position", ["PanTilt"]),
    ("Beam",     "Beam",     ["Beam"]),
    ("Control",  "Control",  ["Control"]),
]

IDENTITY = "{1.000000,0.000000,0.000000,0.000000}" \
           "{0.000000,1.000000,0.000000,0.000000}" \
           "{0.000000,0.000000,1.000000,0.000000}" \
           "{0.000000,0.000000,0.000000,1.000000}"

# Beam sits at the front face of the body, pointing along -Z (GDTF convention).
BEAM_POS = "{1.000000,0.000000,0.000000,0.000000}" \
           "{0.000000,1.000000,0.000000,0.000000}" \
           "{0.000000,0.000000,1.000000,0.100000}" \
           "{0.000000,0.000000,0.000000,1.000000}"

# Channels that ride on the Beam geometry (rendered); the rest ride on Body.
BEAM_ATTRS = {"Dimmer", "ColorAdd_R", "ColorAdd_G", "ColorAdd_B", "ColorAdd_W",
              "Shutter1Strobe", "Zoom"}

# ── Fixtures ──────────────────────────────────────────────────────────────────
# (manufacturer, model, fixture_type, mode_name, beam_angle,
#  [(channel_name, attribute, default_0_255), ...])
FIXTURES = [
    ("UKing", "Par 36 Custom", "Color Changer", "5 Channel", 25, [
        ("Master Dimmer", "Dimmer", 0),
        ("Red", "ColorAdd_R", 0),
        ("Green", "ColorAdd_G", 0),
        ("Blue", "ColorAdd_B", 0),
        ("Strobe", "Shutter1Strobe", 0),
    ]),
    ("UKing", "ZQ-B93 Pinspot RGBW", "Color Changer", "6-channel", 11, [
        ("Total function control", "Control", 255),
        ("Red", "ColorAdd_R", 0),
        ("Green", "ColorAdd_G", 0),
        ("Blue", "ColorAdd_B", 0),
        ("White", "ColorAdd_W", 0),
        ("Auto FX", "Effects", 0),
    ]),
    ("Amazon", "DerbyLaserParty", "Color Changer", "default", 40, [
        ("Master dimmer", "Dimmer", 0),
        ("Strobe", "Shutter1Strobe", 0),
        ("Red", "ColorAdd_R", 0),
        ("Green", "ColorAdd_G", 0),
        ("Blue", "ColorAdd_B", 0),
        ("White", "ColorAdd_W", 0),
        ("Red L", "LaserRed", 0),
        ("Green L", "LaserGreen", 0),
        ("Strobe L", "LaserStrobe", 0),
        ("Marquee Color", "Marquee", 0),
        ("Marquee Speed", "MarqueeSpeed", 0),
        ("Derby Motor", "Rotation", 0),
        ("Laser Motor", "Rotation", 0),
    ]),
    ("Chauvet", "Scorpion Dual RGB", "Laser", "10-Channel", 15, [
        ("Control Mode", "Control", 0),
        ("Pattern", "Pattern", 0),
        ("Color", "ColorMacro", 0),
        ("Strobe", "Shutter1Strobe", 0),
        ("Zoom", "Zoom", 0),
        ("Pan", "Pan", 0),
        ("Tilt", "Tilt", 0),
        ("X-Axis Rolling", "Rotation", 0),
        ("Y-Axis Rolling", "Rotation", 0),
        ("Z-Axis Rolling", "Rotation", 0),
    ]),
    ("Chauvet", "Mini Kinta IRC", "Effect", "4-Channel", 135, [
        ("Control/Operating Mode", "ColorMacro", 0),
        ("Strobe", "Shutter1Strobe", 0),
        ("Motor Rotation", "Rotation", 0),
        ("Auto/Sound Mode", "Effects", 0),
    ]),
    ("Generic", "Dimmer", "Dimmer", "1 Channel", 25, [
        ("Dimmer", "Dimmer", 0),
    ]),
]


def short_name(s: str, n: int = 8) -> str:
    return s.replace(" ", "")[:n]


def build_attribute_defs(used_attrs: set) -> str:
    fg_xml = []
    for fg_name, feat_name, feats in FEATURE_GROUPS:
        feat_xml = "".join(f'<Feature Name="{f}"/>' for f in feats)
        fg_xml.append(
            f'<FeatureGroup Name="{fg_name}" Pretty="{fg_name}">{feat_xml}</FeatureGroup>'
        )

    attr_xml = []
    for name in sorted(used_attrs):
        pretty, feature = ATTRS[name]
        attr_xml.append(
            f'<Attribute Name="{name}" Pretty="{escape(pretty)}" Feature="{feature}"/>'
        )

    return (
        "<AttributeDefinitions>"
        "<ActivationGroups/>"
        f"<FeatureGroups>{''.join(fg_xml)}</FeatureGroups>"
        f"<Attributes>{''.join(attr_xml)}</Attributes>"
        "</AttributeDefinitions>"
    )


def build_models() -> str:
    # Two primitive models BlenderDMX can instantiate directly (Cube / Cylinder).
    return (
        "<Models>"
        '<Model Name="Body" Length="0.2" Width="0.2" Height="0.2" '
        'PrimitiveType="Cube" File=""/>'
        '<Model Name="Beam" Length="0.1" Width="0.1" Height="0.1" '
        'PrimitiveType="Cylinder" File=""/>'
        "</Models>"
    )


def build_geometries(beam_angle: float, power: float) -> str:
    # A Beam geometry with a non-None BeamType is what makes BlenderDMX create a
    # SPOT light. Every geometry must reference a Model with a PrimitiveType or
    # BlenderDMX skips it and the fixture renders nothing.
    beam = (
        f'<Beam Name="Beam" Model="Beam" Position="{BEAM_POS}" '
        f'LampType="LED" PowerConsumption="{power}" LuminousFlux="3000" '
        f'ColorTemperature="6500" BeamAngle="{beam_angle}" FieldAngle="{beam_angle}" '
        f'BeamRadius="0.05" BeamType="Wash" ColorRenderingIndex="100"/>'
    )
    return (
        "<Geometries>"
        f'<Geometry Name="Body" Model="Body" Position="{IDENTITY}">{beam}</Geometry>'
        "</Geometries>"
    )


def _channel_function(attr, name, dmx_from, default, phys_from, phys_to):
    return (
        f'<ChannelFunction Attribute="{attr}" Name="{escape(name)}" '
        f'DMXFrom="{dmx_from}/1" Default="{default}/1" '
        f'PhysicalFrom="{phys_from:.6f}" PhysicalTo="{phys_to:.6f}" '
        f'RealFade="0.000000" RealAcceleration="0.000000" OriginalAttribute=""/>'
    )


def build_dmx_channels(channels: list) -> str:
    out = []
    for offset, (ch_name, attr, default) in enumerate(channels, start=1):
        geom = "Beam" if attr in BEAM_ATTRS else "Body"

        if attr == "Shutter1Strobe":
            # Two functions so BlenderDMX holds the shutter OPEN at rest (DMX 0-7,
            # attr Shutter1) and only strobes from DMX 8 up. A single Shutter1Strobe
            # function reads value 0 as strobe=0, which forces the shutter closed
            # and zeroes the dimmer — darkening the whole fixture.
            cfs = (
                _channel_function("Shutter1", "Open", 0, default, 1.0, 1.0)
                + _channel_function("Shutter1Strobe", "Strobe", 8, default, 0.0, 25.0)
            )
            lc = (
                f'<LogicalChannel Attribute="Shutter1" Snap="No" Master="None" '
                f'MibFade="0.000000" DMXChangeTimeLimit="0.000000">{cfs}</LogicalChannel>'
            )
        else:
            cf = _channel_function(attr, ch_name, 0, default, 0.0, 1.0)
            lc = (
                f'<LogicalChannel Attribute="{attr}" Snap="No" Master="None" '
                f'MibFade="0.000000" DMXChangeTimeLimit="0.000000">{cf}</LogicalChannel>'
            )

        out.append(
            f'<DMXChannel DMXBreak="1" Offset="{offset}" Default="{default}/1" '
            f'Highlight="None" Geometry="{geom}">{lc}</DMXChannel>'
        )
    return "".join(out)


def build_description(mfr, model, ftype, mode, beam_angle, channels, power) -> str:
    used = {attr for _, attr, _ in channels}
    if "Shutter1Strobe" in used:
        used.add("Shutter1")  # the "Open" function declared alongside strobe
    name = f"{mfr} {model}"
    sn = short_name(model)
    long_name = escape(name)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<GDTF DataVersion="1.2">'
        f'<FixtureType Name="{long_name}" ShortName="{escape(sn)}" '
        f'LongName="{long_name}" Manufacturer="{escape(mfr)}" '
        f'Description="{escape(name)} - generated for Cosmos previs" '
        f'FixtureTypeID="00000000-0000-0000-0000-000000000000" '
        f'RefFT="" Thumbnail="">'
        f'{build_attribute_defs(used)}'
        "<Wheels/>"
        "<PhysicalDescriptions/>"
        f'{build_models()}'
        f'{build_geometries(beam_angle, power)}'
        "<DMXModes>"
        f'<DMXMode Name="{escape(mode)}" Geometry="Body">'
        f'<DMXChannels>{build_dmx_channels(channels)}</DMXChannels>'
        "<Relations/>"
        "<FTMacros/>"
        "</DMXMode>"
        "</DMXModes>"
        "<Revisions/>"
        "<FTPresets/>"
        "<Protocols/>"
        "</FixtureType>"
        "</GDTF>"
    )


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    built = []
    for mfr, model, ftype, mode, beam_angle, channels in FIXTURES:
        desc = build_description(mfr, model, ftype, mode, beam_angle, channels, power=36)
        fname = f"{mfr}@{model}@{mode}.gdtf"
        path = os.path.join(OUT_DIR, fname)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("description.xml", desc)
        built.append((fname, len(channels)))

    print(f"Built {len(built)} GDTF profiles in {OUT_DIR}:")
    for fname, n in built:
        print(f"  {fname}  ({n} ch)")


if __name__ == "__main__":
    main()
