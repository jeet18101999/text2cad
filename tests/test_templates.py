"""Build every template (defaults + variants) and sanity-check volumes. Run: python tests/test_templates.py"""

import math
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from text2cad import templates  # noqa: E402
from text2cad.pipeline import OUTPUT_ROOT, build_template  # noqa: E402

CASES = [
    ("rectangular_plate", {}, None),
    ("rectangular_plate", {"holes_x": 3, "corner_radius": 5, "edge_chamfer": 1}, None),
    ("rectangular_plate", {"length": 100, "width": 50, "thickness": 10, "holes_x": 0, "holes_y": 0},
     100 * 50 * 10),
    ("l_bracket", {}, None),
    ("rectangular_plate", {"length": 100, "width": 50, "thickness": 10, "holes_x": 1, "holes_y": 1, "hole_diameter": "M8"},
     100 * 50 * 10 - math.pi * 4.5**2 * 10),
    ("l_bracket", {"leg_a_length": 100, "leg_b_length": 60, "inner_fillet": 0, "outer_radius": 0, "holes_per_leg": 0,
                   "width": 40, "thickness": 5}, (100 * 5 + 55 * 5) * 40),
    ("flange", {}, None),
    ("flange", {"outer_diameter": 50, "flange_thickness": 6, "bore_diameter": 20, "hub_diameter": 30, "hub_length": 19,
                "bolt_holes": 3, "bolt_hole_diameter": 5.5, "bolt_circle_diameter": 40},
     math.pi * (25**2 * 6 + 15**2 * 19 - 10**2 * 25 - 3 * 2.75**2 * 6)),
    ("stepped_shaft", {}, None),
    ("stepped_shaft", {"sections": [[20, 40], [30, 80]], "keyway_section": 2, "keyway_width": 8, "keyway_depth": 4}, None),
    ("spur_gear", {}, None),
    ("spur_gear", {"module": 1.5, "teeth": 40, "hub_diameter": 25, "hub_length": 10, "bore_diameter": 12}, None),
    ("enclosure", {}, None),
    ("enclosure", {"length": 120, "width": 80, "height": 40, "wall_thickness": 2.5, "floor_thickness": 2.5,
                   "outer_fillet": 0}, 120 * 80 * 40 - 115 * 75 * 37.5),
    ("enclosure", {"closed_top": True, "boss_diameter": 7, "boss_height": 15}, None),
    ("standoff_spacer", {"shape": "hex", "outer_size": 10, "length": 25, "bore_diameter": 2.5},
     (math.sqrt(3) / 2 * 10**2 - math.pi * 1.25**2) * 25),
    ("standoff_spacer", {"shape": "round", "outer_size": 12, "length": 20, "bore_diameter": 6, "end_chamfer": 0.5}, None),
    ("standoff_spacer", {"shape": "square", "outer_size": 10, "length": 10, "bore_diameter": 0}, 1000),
    *[("structural_section", {"section": s}, None) for s in ["rectangular_tube", "round_tube", "u_channel", "angle", "t_bar", "i_beam"]],
]

EXPECTED_ERRORS = [
    ("rectangular_plate", {"edge_chamfer": 6}),
    ("rectangular_plate", {"holes_x": 0, "holes_y": 2}),
    ("flange", {"bolt_circle_diameter": 70}),
    ("l_bracket", {"bogus": 1}),
]


def main():
    failures = 0
    for name, params, expected_volume in CASES:
        r = build_template(name, params, out_dir=OUTPUT_ROOT / "_template_test")
        ok = r.ok
        note = r.error or f"bbox={r.stats['bbox_mm']} vol={r.stats['volume_mm3']:.0f}"
        if ok and expected_volume is not None and abs(r.stats["volume_mm3"] - expected_volume) > 0.01 * expected_volume:
            ok, note = False, f"volume {r.stats['volume_mm3']:.0f} != expected {expected_volume:.0f}"
        if ok and r.stats["solids"] != 1:
            ok, note = False, f"{r.stats['solids']} solids (expected 1)"
        if ok:
            note += f"\n      {templates.describe(name, r.params)}"
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name} {params}: {note}")

    for name, params in EXPECTED_ERRORS:
        r = build_template(name, params, out_dir=OUTPUT_ROOT / "_template_test")
        ok = not r.ok and r.error
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  rejects {name} {params}: {r.error}")

    assert "## spur_gear" in templates.catalog()
    shutil.rmtree(OUTPUT_ROOT / "_template_test", ignore_errors=True)
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
