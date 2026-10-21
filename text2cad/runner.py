"""Run generated CadQuery code in a child process and export STEP/STL + preview mesh.

Invoked as: python -m text2cad.runner <script.py> <output_dir> [--no-bbox-check]
Writes <output_dir>/model.step, model.stl, mesh.json and prints a JSON status line.
The EXPECTED_BBOX check guards free-form AI code; template scripts skip it.
"""

import inspect
import json
import re
import sys
import traceback
from pathlib import Path


def api_hint(message: str) -> str:
    """If the error is a misuse of a CadQuery method, return its real signature to help the LLM fix it."""
    import cadquery as cq

    match = re.search(r"(Workplane|Shape|Solid|Vector)\.(\w+)\(\)", message) or \
        re.search(r"'(Workplane|Shape|Solid|Vector)' object has no attribute '(\w+)'", message)
    if not match:
        return ""
    cls = getattr(cq, match.group(1))
    name = match.group(2)
    if hasattr(cls, name):
        try:
            return f"\nCorrect signature: {match.group(1)}.{name}{inspect.signature(getattr(cls, name))}"
        except (TypeError, ValueError):
            return ""
    similar = [m for m in dir(cls) if not m.startswith("_") and (name.lower()[:4] in m.lower())]
    return f"\n{match.group(1)} has no method '{name}'." + (f" Similar methods: {', '.join(similar[:8])}" if similar else "")


def main() -> None:
    script_path, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        import cadquery as cq

        namespace: dict = {"__name__": "__text2cad__"}
        exec(compile(script_path.read_text(encoding="utf-8"), "generated_part.py", "exec"), namespace)

        result = namespace.get("result")
        if result is None:
            raise ValueError("The script did not assign the final part to a variable named `result`.")
        shape = result.val() if isinstance(result, cq.Workplane) else result
        if isinstance(result, cq.Workplane) and len(result.vals()) > 1:
            shape = cq.Compound.makeCompound([v for v in result.vals() if isinstance(v, cq.Shape)])
        if not isinstance(shape, cq.Shape):
            raise TypeError(f"`result` must be a CadQuery solid, got {type(shape).__name__}.")
        if not shape.isValid():
            raise ValueError("The generated solid is not valid geometry (check booleans and fillets).")

        bb = shape.BoundingBox()
        actual = (bb.xlen, bb.ylen, bb.zlen)
        expected = namespace.get("EXPECTED_BBOX")
        if "--no-bbox-check" not in sys.argv:
            if expected is None:
                raise ValueError("The script did not define EXPECTED_BBOX = (x, y, z).")
            # Compare sorted extents so a part that is merely oriented differently still passes.
            for want, got in zip(sorted(float(v) for v in expected), sorted(actual)):
                # Loose on purpose: catches gross errors (missing/unjoined features, flat instead of 3D)
                # without rejecting correct parts whose extents the LLM estimated slightly wrong.
                if abs(want - got) > max(1.5, 0.2 * want):
                    raise ValueError(
                        f"Geometry does not match the intended size: EXPECTED_BBOX is "
                        f"{tuple(round(float(v), 2) for v in expected)} but the built part measures "
                        f"({actual[0]:.2f}, {actual[1]:.2f}, {actual[2]:.2f}) in X, Y, Z. "
                        "Either the geometry or EXPECTED_BBOX is wrong. Check each feature's size and position "
                        "against the description. If the geometry is right, correct EXPECTED_BBOX to the true "
                        "extents (hexagon corners, bolt-circle holes, fillets and chamfers all affect it)."
                    )

        cq.exporters.export(shape, str(out_dir / "model.step"))
        cq.exporters.export(shape, str(out_dir / "model.stl"), tolerance=0.05, angularTolerance=0.1)
        vertices, triangles = shape.tessellate(0.1, 0.2)
        (out_dir / "mesh.json").write_text(
            json.dumps({"vertices": [v.toTuple() for v in vertices], "triangles": triangles})
        )

        print(json.dumps({
            "ok": True,
            "bbox_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
            "volume_mm3": round(shape.Volume(), 3),
            "solids": len(shape.Solids()),
        }))
    except Exception as e:
        tb = traceback.format_exc(limit=-3)
        error = f"{type(e).__name__}: {e}"
        try:
            error += api_hint(error)
        except Exception:
            pass
        print(json.dumps({"ok": False, "error": error, "traceback": tb}))


if __name__ == "__main__":
    main()
