"""Tested, parametric generators for common engineering parts.

The AI only has to pick a template and fill in numbers, which small local models do reliably.
Every function is self-contained (uses only the helpers below) so it can be exported as a
standalone CadQuery script.
"""

import inspect
import json
import math
import typing
from typing import Literal

import cadquery as cq


# ---------------------------------------------------------------- helpers

def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _linspace(start, end, count):
    if count == 1:
        return [(start + end) / 2]
    return [start + (end - start) * i / (count - 1) for i in range(count)]


def _even_positions(start, end, count):
    step = (end - start) / count
    return [start + step * (i + 0.5) for i in range(count)]


def _bolt_circle(count, circle_diameter):
    return [
        (circle_diameter / 2 * math.cos(2 * math.pi * i / count), circle_diameter / 2 * math.sin(2 * math.pi * i / count))
        for i in range(count)
    ]


def _z_cylinders(points, diameter, z0, z1):
    """Solid cylinders along Z from z0 to z1 at the given XY points (used as hole cutters)."""
    return cq.Workplane("XY").workplane(offset=z0).pushPoints(points).circle(diameter / 2).extrude(z1 - z0)


def _involute_gear_outline(module, teeth, pressure_angle_deg, flank_points=12):
    alpha = math.radians(pressure_angle_deg)
    r_pitch = module * teeth / 2
    r_base = r_pitch * math.cos(alpha)
    r_tip = r_pitch + module
    r_root = r_pitch - 1.25 * module

    def inv(a):
        return math.tan(a) - a

    def half_angle(r):
        # Angle from tooth centreline to the flank at radius r.
        return math.pi / (2 * teeth) + inv(alpha) - inv(math.acos(min(1.0, r_base / r)))

    _require(half_angle(r_tip) > 0, "Teeth are pointed at this size; use more teeth or a smaller pressure angle.")
    r_flank_start = max(r_base, r_root)
    radii = [r_flank_start + (r_tip - r_flank_start) * i / flank_points for i in range(flank_points + 1)]
    pitch = 2 * math.pi / teeth
    _require(pitch - 2 * half_angle(r_flank_start) > 0, "Tooth spaces close up at the root; use more teeth.")

    def polar(r, a):
        return (r * math.cos(a), r * math.sin(a))

    points = []
    for i in range(teeth):
        c = i * pitch
        if r_root < r_base:
            points.append(polar(r_root, c - half_angle(r_base)))
        points += [polar(r, c - half_angle(r)) for r in radii]
        points += [polar(r_tip, c + t * half_angle(r_tip)) for t in (-0.5, 0.0, 0.5)]
        points += [polar(r, c + half_angle(r)) for r in reversed(radii)]
        if r_root < r_base:
            points.append(polar(r_root, c + half_angle(r_base)))
        root_start = c + half_angle(r_flank_start)
        root_end = c + pitch - half_angle(r_flank_start)
        points += [polar(r_root, root_start + (root_end - root_start) * t) for t in (0.25, 0.5, 0.75)]
    return points, r_root


# ---------------------------------------------------------------- templates

def rectangular_plate(
    length: float = 100.0,
    width: float = 60.0,
    thickness: float = 10.0,
    holes_x: int = 2,
    holes_y: int = 2,
    hole_diameter: float = 6.6,
    hole_edge_offset: float = 8.0,
    corner_radius: float = 0.0,
    edge_chamfer: float = 0.0,
):
    _require(min(length, width, thickness) > 0, "length, width and thickness must be positive.")
    _require(corner_radius < min(length, width) / 2, "corner_radius must be less than half the shorter side.")
    _require(edge_chamfer < thickness / 2, "edge_chamfer must be less than half the thickness.")
    part = cq.Workplane("XY").box(length, width, thickness, centered=(True, True, False))
    if corner_radius > 0:
        part = part.edges("|Z").fillet(corner_radius)
    if edge_chamfer > 0:
        part = part.faces(">Z or <Z").edges().chamfer(edge_chamfer)
    _require((holes_x > 0) == (holes_y > 0),
             "holes_x and holes_y must both be 0 (no holes) or both at least 1 (e.g. a single row of 2 holes is holes_x=2, holes_y=1).")
    if holes_x > 0 and holes_y > 0 and hole_diameter > 0:
        _require(hole_edge_offset > hole_diameter / 2, "hole_edge_offset must be larger than the hole radius.")
        _require(holes_x == 1 or 2 * hole_edge_offset < length, "hole_edge_offset is too large for the plate length.")
        _require(holes_y == 1 or 2 * hole_edge_offset < width, "hole_edge_offset is too large for the plate width.")
        xs = _linspace(-length / 2 + hole_edge_offset, length / 2 - hole_edge_offset, holes_x)
        ys = _linspace(-width / 2 + hole_edge_offset, width / 2 - hole_edge_offset, holes_y)
        part = part.cut(_z_cylinders([(x, y) for x in xs for y in ys], hole_diameter, -1, thickness + 1))
    return part


def l_bracket(
    leg_a_length: float = 80.0,
    leg_b_length: float = 80.0,
    width: float = 40.0,
    thickness: float = 5.0,
    inner_fillet: float = 3.0,
    outer_radius: float = 0.0,
    holes_per_leg: int = 2,
    hole_diameter: float = 6.6,
):
    _require(min(leg_a_length, leg_b_length) > thickness, "Both legs must be longer than the thickness.")
    _require(inner_fillet < min(leg_a_length, leg_b_length) - thickness, "inner_fillet is too large for the legs.")
    _require(outer_radius < thickness + inner_fillet or outer_radius == 0, "outer_radius is too large.")
    _require(hole_diameter < width, "hole_diameter must be smaller than the width.")
    part = (
        cq.Workplane("XZ")
        .polyline([(0, 0), (leg_a_length, 0), (leg_a_length, thickness), (thickness, thickness),
                   (thickness, leg_b_length), (0, leg_b_length)])
        .close()
        .extrude(width / 2, both=True)
    )
    if inner_fillet > 0:
        part = part.edges(cq.selectors.NearestToPointSelector((thickness, 0, thickness))).fillet(inner_fillet)
    if outer_radius > 0:
        part = part.edges(cq.selectors.NearestToPointSelector((0, 0, 0))).fillet(outer_radius)
    if holes_per_leg > 0 and hole_diameter > 0:
        start = thickness + inner_fillet
        _require((leg_a_length - start) / holes_per_leg > hole_diameter, "Too many holes for leg A.")
        _require((leg_b_length - start) / holes_per_leg > hole_diameter, "Too many holes for leg B.")
        xs = _even_positions(start, leg_a_length, holes_per_leg)
        zs = _even_positions(start, leg_b_length, holes_per_leg)
        leg_a_holes = cq.Workplane("XY").workplane(offset=-1).pushPoints([(x, 0) for x in xs]) \
            .circle(hole_diameter / 2).extrude(thickness + 2)
        leg_b_holes = cq.Workplane("YZ").workplane(offset=-1).pushPoints([(0, z) for z in zs]) \
            .circle(hole_diameter / 2).extrude(thickness + 2)
        part = part.cut(leg_a_holes).cut(leg_b_holes)
    return part


def flange(
    outer_diameter: float = 150.0,
    flange_thickness: float = 16.0,
    bore_diameter: float = 60.0,
    hub_diameter: float = 0.0,
    hub_length: float = 0.0,
    bolt_holes: int = 4,
    bolt_hole_diameter: float = 18.0,
    bolt_circle_diameter: float = 120.0,
):
    has_hub = hub_diameter > 0 and hub_length > 0
    _require(bore_diameter < outer_diameter, "bore_diameter must be smaller than outer_diameter.")
    part = cq.Workplane("XY").circle(outer_diameter / 2).extrude(flange_thickness)
    total = flange_thickness
    if has_hub:
        _require(bore_diameter < hub_diameter <= outer_diameter, "hub_diameter must be between the bore and the outer diameter.")
        part = part.union(_z_cylinders([(0, 0)], hub_diameter, flange_thickness, flange_thickness + hub_length))
        total += hub_length
    if bore_diameter > 0:
        part = part.cut(_z_cylinders([(0, 0)], bore_diameter, -1, total + 1))
    if bolt_holes > 0 and bolt_hole_diameter > 0:
        inner = hub_diameter if has_hub else bore_diameter
        _require(bolt_circle_diameter - bolt_hole_diameter > inner, "Bolt holes overlap the bore/hub; increase bolt_circle_diameter.")
        _require(bolt_circle_diameter + bolt_hole_diameter < outer_diameter, "Bolt holes break the outer edge; decrease bolt_circle_diameter.")
        part = part.cut(_z_cylinders(_bolt_circle(bolt_holes, bolt_circle_diameter), bolt_hole_diameter, -1, flange_thickness + 1))
    return part


def stepped_shaft(
    sections: list = ((20.0, 30.0), (25.0, 60.0), (20.0, 30.0)),
    end_chamfer: float = 1.0,
    keyway_section: int = 0,
    keyway_width: float = 6.0,
    keyway_depth: float = 3.5,
    keyway_length: float = 0.0,
):
    _require(len(sections) >= 1, "At least one section is required.")
    part, z, starts = None, 0.0, []
    for diameter, length in sections:
        _require(diameter > 0 and length > 0, "Section diameters and lengths must be positive.")
        starts.append(z)
        segment = _z_cylinders([(0, 0)], diameter, z, z + length)
        part = segment if part is None else part.union(segment)
        z += length
    if end_chamfer > 0:
        _require(end_chamfer < min(sections[0][0], sections[-1][0]) / 2, "end_chamfer is too large for the end diameters.")
        part = part.faces("<Z").edges().chamfer(end_chamfer).faces(">Z").edges().chamfer(end_chamfer)
    if keyway_section > 0:
        _require(keyway_section <= len(sections), "keyway_section must be a valid 1-based section number.")
        diameter, length = sections[keyway_section - 1]
        key_len = keyway_length or 0.7 * length
        _require(keyway_depth < diameter / 2 and keyway_width < diameter, "Keyway is too big for that section.")
        _require(key_len <= length, "keyway_length is longer than the section.")
        cutter = (
            cq.Workplane("XY")
            .box(keyway_width, keyway_depth + 1, key_len, centered=(True, False, True))
            .translate((0, diameter / 2 - keyway_depth, starts[keyway_section - 1] + length / 2))
        )
        part = part.cut(cutter)
    return part.rotate((0, 0, 0), (0, 1, 0), 90)  # shaft axis along X


def spur_gear(
    module: float = 2.0,
    teeth: int = 20,
    face_width: float = 15.0,
    pressure_angle: float = 20.0,
    bore_diameter: float = 10.0,
    keyway_width: float = 3.0,
    keyway_depth: float = 1.4,
    hub_diameter: float = 0.0,
    hub_length: float = 0.0,
):
    _require(teeth >= 8, "Use at least 8 teeth.")
    outline, r_root = _involute_gear_outline(module, teeth, pressure_angle)
    part = cq.Workplane("XY").polyline(outline).close().extrude(face_width)
    total = face_width
    if hub_diameter > 0 and hub_length > 0:
        _require(hub_diameter < 2 * r_root, "hub_diameter must be smaller than the gear root diameter.")
        part = part.union(_z_cylinders([(0, 0)], hub_diameter, face_width, face_width + hub_length))
        total += hub_length
    if bore_diameter > 0:
        _require(bore_diameter + 2 * keyway_depth < 2 * r_root - 2 * module, "Bore is too large for this gear.")
        part = part.cut(_z_cylinders([(0, 0)], bore_diameter, -1, total + 1))
        if keyway_width > 0 and keyway_depth > 0:
            cutter = cq.Workplane("XY").box(keyway_width, bore_diameter / 2 + keyway_depth, total + 2, centered=(True, False, False)) \
                .translate((0, 0, -1))
            part = part.cut(cutter)
    return part


def enclosure(
    length: float = 120.0,
    width: float = 80.0,
    height: float = 40.0,
    wall_thickness: float = 2.5,
    floor_thickness: float = 2.5,
    outer_fillet: float = 3.0,
    closed_top: bool = False,
    boss_diameter: float = 0.0,
    boss_hole_diameter: float = 2.5,
    boss_height: float = 10.0,
):
    _require(2 * wall_thickness < min(length, width), "Walls are too thick for this size.")
    top = wall_thickness if closed_top else 0.0
    _require(floor_thickness + top < height, "Floor/top are too thick for this height.")
    _require(outer_fillet < min(length, width) / 2, "outer_fillet is too large.")
    part = cq.Workplane("XY").box(length, width, height, centered=(True, True, False))
    if outer_fillet > 0:
        part = part.edges("|Z").fillet(outer_fillet)
    cavity_top = height - top if closed_top else height + 1
    cavity = (
        cq.Workplane("XY")
        .box(length - 2 * wall_thickness, width - 2 * wall_thickness, cavity_top - floor_thickness, centered=(True, True, False))
        .translate((0, 0, floor_thickness))
    )
    inner_fillet = outer_fillet - wall_thickness
    if inner_fillet > 0.1:
        cavity = cavity.edges("|Z").fillet(inner_fillet)
    part = part.cut(cavity)
    if boss_diameter > 0:
        _require(boss_hole_diameter < boss_diameter, "boss_hole_diameter must be smaller than boss_diameter.")
        _require(floor_thickness + boss_height <= height - top, "Bosses are taller than the cavity.")
        dx = length / 2 - wall_thickness - boss_diameter / 2
        dy = width / 2 - wall_thickness - boss_diameter / 2
        points = [(sx * dx, sy * dy) for sx in (-1, 1) for sy in (-1, 1)]
        part = part.union(_z_cylinders(points, boss_diameter, floor_thickness, floor_thickness + boss_height))
        if boss_hole_diameter > 0:
            part = part.cut(_z_cylinders(points, boss_hole_diameter, floor_thickness, floor_thickness + boss_height + 1))
    return part


def structural_section(
    section: Literal["rectangular_tube", "round_tube", "u_channel", "angle", "t_bar", "i_beam"] = "rectangular_tube",
    length: float = 500.0,
    width: float = 50.0,
    height: float = 50.0,
    thickness: float = 4.0,
):
    _require(2 * thickness < min(width, height), "thickness is too large for this section.")
    w, h, t = width, height, thickness
    wp = cq.Workplane("YZ")  # profile in Y/Z, extruded along X
    if section == "rectangular_tube":
        return wp.rect(w, h).rect(w - 2 * t, h - 2 * t).extrude(length)
    if section == "round_tube":
        return wp.circle(w / 2).circle(w / 2 - t).extrude(length)
    profiles = {
        "u_channel": [(-w / 2, 0), (w / 2, 0), (w / 2, h), (w / 2 - t, h), (w / 2 - t, t), (-w / 2 + t, t), (-w / 2 + t, h), (-w / 2, h)],
        "angle": [(0, 0), (w, 0), (w, t), (t, t), (t, h), (0, h)],
        "t_bar": [(-t / 2, 0), (t / 2, 0), (t / 2, h - t), (w / 2, h - t), (w / 2, h), (-w / 2, h), (-w / 2, h - t), (-t / 2, h - t)],
        "i_beam": [(-w / 2, 0), (w / 2, 0), (w / 2, t), (t / 2, t), (t / 2, h - t), (w / 2, h - t), (w / 2, h),
                   (-w / 2, h), (-w / 2, h - t), (-t / 2, h - t), (-t / 2, t), (-w / 2, t)],
    }
    _require(section in profiles, f"Unknown section '{section}'.")
    return wp.polyline(profiles[section]).close().extrude(length)


def standoff_spacer(
    shape: Literal["round", "hex", "square"] = "hex",
    outer_size: float = 10.0,
    length: float = 25.0,
    bore_diameter: float = 3.4,
    end_chamfer: float = 0.0,
):
    _require(outer_size > 0 and length > 0, "outer_size and length must be positive.")
    _require(bore_diameter < outer_size - 1, "bore_diameter is too large for outer_size.")
    wp = cq.Workplane("XY")
    if shape == "round":
        part = wp.circle(outer_size / 2).extrude(length)
    elif shape == "hex":
        part = wp.polygon(6, outer_size, circumscribed=True).extrude(length)  # outer_size = across flats
    else:
        part = wp.rect(outer_size, outer_size).extrude(length)
    if end_chamfer > 0:
        _require(end_chamfer < (outer_size - bore_diameter) / 4, "end_chamfer is too large.")
        part = part.faces("<Z or >Z").edges().chamfer(end_chamfer)
    if bore_diameter > 0:
        part = part.cut(_z_cylinders([(0, 0)], bore_diameter, -1, length + 1))
    return part


# ---------------------------------------------------------------- plain-English descriptions
# Written from the actual parameters so an independent check can compare the built part with the request.

def _g(v):
    return f"{v:g}"


def _describe_rectangular_plate(p):
    text = f"A solid rectangular plate {_g(p['length'])} (X) × {_g(p['width'])} (Y) × {_g(p['thickness'])} (Z) mm."
    if p["holes_x"] and p["holes_y"] and p["hole_diameter"]:
        text += (f" {p['holes_x'] * p['holes_y']} through holes of {_g(p['hole_diameter'])} mm diameter, all running vertically "
                 f"through the thickness (along Z), in a {p['holes_x']} × {p['holes_y']} grid with outer hole centres "
                 f"{_g(p['hole_edge_offset'])} mm from the edges.")
    else:
        text += " No holes."
    text += f" Vertical corner radius {_g(p['corner_radius'])} mm, top/bottom edge chamfer {_g(p['edge_chamfer'])} mm."
    return text


def _describe_l_bracket(p):
    text = (f"An L-shaped bracket bent from {_g(p['thickness'])} mm plate, {_g(p['width'])} mm wide: one leg "
            f"{_g(p['leg_a_length'])} mm long and one leg {_g(p['leg_b_length'])} mm long (outside dimensions), inside bend "
            f"fillet {_g(p['inner_fillet'])} mm, outside bend radius {_g(p['outer_radius'])} mm.")
    if p["holes_per_leg"] and p["hole_diameter"]:
        text += (f" Each leg has {p['holes_per_leg']} through hole(s) of {_g(p['hole_diameter'])} mm diameter, "
                 "evenly spaced in a single row along the leg's centreline.")
    return text


def _describe_flange(p):
    text = f"A round disc {_g(p['outer_diameter'])} mm diameter × {_g(p['flange_thickness'])} mm thick."
    if p["hub_diameter"] and p["hub_length"]:
        text += f" A {_g(p['hub_diameter'])} mm diameter hub protrudes {_g(p['hub_length'])} mm from one face."
    text += f" Central through bore {_g(p['bore_diameter'])} mm." if p["bore_diameter"] else " No central bore."
    if p["bolt_holes"] and p["bolt_hole_diameter"]:
        text += (f" {p['bolt_holes']} axial through holes of {_g(p['bolt_hole_diameter'])} mm on a "
                 f"{_g(p['bolt_circle_diameter'])} mm pitch circle.")
    return text


def _describe_stepped_shaft(p):
    sections = ", then ".join(f"{_g(d)} mm diameter for {_g(l)} mm" for d, l in p["sections"])
    text = f"A solid round shaft: {sections}. End chamfers {_g(p['end_chamfer'])} mm."
    if p["keyway_section"]:
        text += (f" One keyway {_g(p['keyway_width'])} mm wide × {_g(p['keyway_depth'])} mm deep on section "
                 f"{p['keyway_section']}.")
    return text


def _describe_spur_gear(p):
    text = (f"An involute spur gear: module {_g(p['module'])}, {p['teeth']} teeth, outside diameter "
            f"{_g(p['module'] * (p['teeth'] + 2))} mm, face width {_g(p['face_width'])} mm, pressure angle {_g(p['pressure_angle'])}°.")
    if p["bore_diameter"]:
        text += f" Central bore {_g(p['bore_diameter'])} mm"
        text += (f" with a {_g(p['keyway_width'])} mm × {_g(p['keyway_depth'])} mm keyway." if p["keyway_width"] and p["keyway_depth"]
                 else ", no keyway.")
    if p["hub_diameter"] and p["hub_length"]:
        text += f" A {_g(p['hub_diameter'])} mm hub protrudes {_g(p['hub_length'])} mm from one face."
    return text


def _describe_enclosure(p):
    text = (f"A hollow rectangular box, outside {_g(p['length'])} × {_g(p['width'])} × {_g(p['height'])} mm, walls "
            f"{_g(p['wall_thickness'])} mm, floor {_g(p['floor_thickness'])} mm, "
            f"{'closed (sealed) top' if p['closed_top'] else 'open top'}, vertical outside corner radius {_g(p['outer_fillet'])} mm.")
    if p["boss_diameter"]:
        text += (f" Four {_g(p['boss_diameter'])} mm screw bosses, {_g(p['boss_height'])} mm tall, in the inside corners, "
                 f"each with a {_g(p['boss_hole_diameter'])} mm hole.")
    return text


def _describe_standoff_spacer(p):
    size = {"round": "outer diameter", "hex": "across flats", "square": "side"}[p["shape"]]
    text = f"A {p['shape']} standoff/spacer, {_g(p['outer_size'])} mm {size}, {_g(p['length'])} mm long"
    text += f", with a {_g(p['bore_diameter'])} mm through hole along its axis." if p["bore_diameter"] else ", solid."
    return text + f" End chamfers {_g(p['end_chamfer'])} mm."


def _describe_structural_section(p):
    size = f"{_g(p['width'])} mm outer diameter" if p["section"] == "round_tube" else f"{_g(p['width'])} × {_g(p['height'])} mm"
    return f"A straight {p['section'].replace('_', ' ')} profile, {size}, {_g(p['thickness'])} mm thick, {_g(p['length'])} mm long."


def describe(name: str, params: dict) -> str:
    return TEMPLATES[name]["describe"](params) + " The part has no other features."


# ---------------------------------------------------------------- registry

HELPERS = [_require, _linspace, _even_positions, _bolt_circle, _z_cylinders, _involute_gear_outline]

TEMPLATES = {
    "rectangular_plate": {
        "func": rectangular_plate,
        "describe": _describe_rectangular_plate,
        "description": "Flat rectangular plate lying in XY with an optional grid of through holes, rounded corners and edge chamfers. Also for base plates, mounting plates, spacers, shims.",
        "params": {
            "holes_x": "number of hole columns along the length (0 = no holes)",
            "holes_y": "number of hole rows along the width",
            "hole_edge_offset": "distance from plate edge to outermost hole centres",
            "corner_radius": "radius of the 4 vertical corners (0 = sharp)",
            "edge_chamfer": "chamfer on top and bottom outer edges (0 = none)",
        },
    },
    "l_bracket": {
        "func": l_bracket,
        "describe": _describe_l_bracket,
        "description": "L-shaped / angle mounting bracket made from plate, with a row of holes along the centreline of each leg.",
        "params": {
            "leg_a_length": "horizontal leg length (outside)",
            "leg_b_length": "vertical leg length (outside)",
            "width": "bracket width (along the bend)",
            "inner_fillet": "radius on the inside of the bend",
            "outer_radius": "radius on the outside of the bend (0 = sharp)",
            "holes_per_leg": "holes in each leg, evenly spaced along its centreline (0 = none)",
        },
    },
    "flange": {
        "func": flange,
        "describe": _describe_flange,
        "description": "Round disc with centre bore and bolt holes on a pitch circle, with an optional hub on one side. For pipe flanges, flanged bushings, round covers, washers, spacers, pulley blanks.",
        "params": {
            "flange_thickness": "disc thickness",
            "hub_diameter": "hub outer diameter (0 = no hub)",
            "hub_length": "how far the hub protrudes from the flange face",
            "bolt_holes": "number of bolt holes (0 = none)",
            "bolt_circle_diameter": "pitch circle diameter (PCD) of the bolt holes",
        },
    },
    "stepped_shaft": {
        "func": stepped_shaft,
        "describe": _describe_stepped_shaft,
        "description": "Round shaft (plain or stepped) along X built from consecutive sections, with end chamfers and an optional keyway. For shafts, axles, pins, dowels.",
        "params": {
            "sections": "list of [diameter, length] pairs from one end to the other",
            "keyway_section": "1-based section number that gets a keyway (0 = no keyway)",
            "keyway_depth": "keyway depth into the shaft",
            "keyway_length": "keyway length (0 = 70% of the section length)",
        },
    },
    "spur_gear": {
        "func": spur_gear,
        "describe": _describe_spur_gear,
        "description": "Involute spur gear with bore, keyway and optional hub. Outside diameter = module * (teeth + 2); pitch diameter = module * teeth.",
        "params": {
            "module": "gear module in mm (tooth size)",
            "teeth": "number of teeth",
            "pressure_angle": "pressure angle in degrees (usually 20)",
            "bore_diameter": "centre bore (0 = solid)",
            "keyway_width": "bore keyway width (0 = none)",
            "keyway_depth": "keyway depth beyond the bore surface",
            "hub_diameter": "hub diameter (0 = no hub)",
            "hub_length": "hub protrusion from the gear face",
        },
    },
    "enclosure": {
        "func": enclosure,
        "describe": _describe_enclosure,
        "description": "Hollow rectangular box / housing / tray with uniform walls, rounded vertical corners, open or closed top, and optional corner screw bosses inside.",
        "params": {
            "height": "overall outside height",
            "outer_fillet": "radius applied to all 4 vertical outside corner edges (0 = sharp)",
            "closed_top": "true = sealed hollow box, false = open top",
            "boss_diameter": "diameter of 4 internal corner screw bosses (0 = none)",
            "boss_hole_diameter": "pilot hole in each boss (e.g. 2.5 for M3 self-tapping)",
            "boss_height": "boss height above the floor",
        },
    },
    "standoff_spacer": {
        "func": standoff_spacer,
        "describe": _describe_standoff_spacer,
        "description": "Round, hexagonal or square standoff / spacer / bush / short bar along Z with an optional through bore.",
        "params": {
            "outer_size": "outer diameter (round), across-flats size (hex) or side length (square)",
            "bore_diameter": "through hole along the axis (0 = solid)",
            "end_chamfer": "chamfer on both ends (0 = none)",
        },
    },
    "structural_section": {
        "func": structural_section,
        "describe": _describe_structural_section,
        "description": "Straight length of structural profile along X: rectangular/square tube, round tube, U channel, equal/unequal angle, T bar or I beam.",
        "params": {
            "section": "profile type",
            "width": "profile width (outer diameter for round_tube; flange width for t_bar/i_beam)",
            "height": "profile height (ignored for round_tube)",
            "thickness": "wall / flange / web thickness",
        },
    },
}


def _type_label(annotation):
    if typing.get_origin(annotation) is Literal:
        return "one of " + ", ".join(typing.get_args(annotation))
    return {float: "number", int: "integer", bool: "true/false", list: "list"}[annotation]


def parameters(name: str) -> dict:
    """{param: (annotation, default)} for a template."""
    sig = inspect.signature(TEMPLATES[name]["func"])
    return {p.name: (p.annotation, p.default) for p in sig.parameters.values()}


def catalog() -> str:
    lines = []
    for name, spec in TEMPLATES.items():
        lines.append(f"## {name}\n{spec['description']}")
        for pname, (ann, default) in parameters(name).items():
            default_repr = json.dumps([list(s) for s in default]) if ann is list else json.dumps(default)
            help_text = spec["params"].get(pname, pname.replace("_", " "))
            lines.append(f"- {pname} ({_type_label(ann)}, default {default_repr}): {help_text}")
        lines.append("")
    return "\n".join(lines)


# ISO 273 medium-series clearance holes.
CLEARANCE_HOLES = {
    "M2": 2.4, "M2.5": 2.9, "M3": 3.4, "M4": 4.5, "M5": 5.5, "M6": 6.6, "M8": 9.0, "M10": 11.0,
    "M12": 13.5, "M14": 15.5, "M16": 17.5, "M20": 22.0, "M24": 26.0, "M30": 33.0,
}


def _to_float(value):
    """Numbers, numeric strings ('6.6', '6.6 mm') or thread sizes ('M6' -> ISO 273 clearance diameter)."""
    if isinstance(value, str):
        text = value.strip().upper().replace("MM", "").strip()
        if text in CLEARANCE_HOLES:
            return CLEARANCE_HOLES[text]
        return float(text)
    return float(value)


def coerce_params(name: str, params: dict) -> dict:
    """Validate/convert LLM-supplied params; fill defaults. Raises ValueError with a helpful message."""
    if name not in TEMPLATES:
        raise ValueError(f"Unknown template '{name}'. Valid: {', '.join(TEMPLATES)}.")
    spec = parameters(name)
    unknown = set(params) - set(spec)
    if unknown:
        raise ValueError(f"Unknown parameter(s) for {name}: {', '.join(sorted(unknown))}. Valid: {', '.join(spec)}.")
    out = {}
    for pname, (ann, default) in spec.items():
        if pname not in params:
            out[pname] = default
            continue
        value = params.get(pname, default)
        try:
            if typing.get_origin(ann) is Literal:
                if value not in typing.get_args(ann):
                    raise ValueError(f"must be one of {typing.get_args(ann)}")
            elif ann is bool:
                value = value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes")
            elif ann is int:
                value = int(round(_to_float(value)))
            elif ann is float:
                value = _to_float(value)
            elif ann is list:
                value = [
                    (_to_float(s["diameter"]), _to_float(s["length"])) if isinstance(s, dict) else (_to_float(s[0]), _to_float(s[1]))
                    for s in value
                ]
        except (TypeError, ValueError, KeyError, IndexError) as e:
            raise ValueError(f"Invalid value for {pname}: {value!r} ({e})") from e
        out[pname] = value
    return out


def standalone_script(name: str, params: dict) -> str:
    """A self-contained CadQuery script that rebuilds the part (no Text2CAD needed)."""
    args = ",\n".join(f"    {k}={v!r}" for k, v in params.items())
    parts = [
        f'"""Generated by Text2CAD from the \'{name}\' template. Edit the values at the bottom to change the part."""',
        "import math\nfrom typing import Literal\n\nimport cadquery as cq\n",
        *[inspect.getsource(h) for h in HELPERS],
        inspect.getsource(TEMPLATES[name]["func"]),
        f"result = {name}(\n{args},\n)\n",
    ]
    return "\n\n".join(parts)
