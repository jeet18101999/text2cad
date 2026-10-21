"""LLM calls: pick a part template (router) or write free-form CadQuery code.

Backends: a local Ollama model (default) or Claude via the Anthropic API.
"""

import json
import os
import re

from . import templates

DEFAULT_BACKEND = os.getenv("TEXT2CAD_BACKEND", "ollama")  # "ollama" or "claude"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-coder:30b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
CLAUDE_MODEL = "claude-opus-5"


class GenerationError(RuntimeError):
    pass


# ---------------------------------------------------------------- prompts

ROUTER_PROMPT = f"""You are a mechanical design engineer. You convert a description of a part into parameters for a library of tested, parametric CAD templates.

Available templates (all dimensions in millimetres):

{templates.catalog()}
Reply with JSON only, in exactly this shape:
{{"template": "<best template name, or custom>",
 "feature_mapping": [{{"feature": "<a feature the user asked for>", "parameter": "<the template parameter that produces it, 'shape' for the overall part type, or null>"}}],
 "params": {{...}},
 "summary": "<one or two sentences describing the part and any assumptions you made>"}}

Rules:
- feature_mapping must list every physical feature the user asked for — overall size, each distinct set of holes, bore, slot, fillet, boss, ... Describe each feature once, including all its details (e.g. "two M6 holes on the centreline of each leg" is one feature, even if it needs several parameters) — and name the exact parameter of the chosen template that produces it. Use null only when no parameter of that template can produce the feature — for example a cross bore through a block has no parameter in rectangular_plate. If any feature maps to null, set template to "custom".
- Put every dimension the user gave into params, converted to millimetres. Omitted parameters use the defaults shown, but choose sensible proportional values when the defaults would not fit the part.
- For bolt/screw clearance holes, give the thread size as a string, e.g. "hole_diameter": "M6" — it is converted to the ISO clearance diameter automatically. Use a number only when the user gives an explicit hole diameter or for pilot/tapping holes (M2.5 2.0, M3 2.5, M4 3.3 mm).
- For hole grids, put more holes along the longer side (e.g. six holes on a long plate = 3 along the length x 2 across the width).
- If a current design is given, the request modifies it: keep its parameters and change only what the request asks for."""

CODE_PROMPT = """You are an expert mechanical design engineer who writes CadQuery (Python) code.

Given a description of an engineering part, write a single self-contained Python script that builds it.

Rules:
- `import cadquery as cq` and `import math` at the top (use `math.cos`, `math.radians`, ... — never bare `cos`). No other imports, no file I/O, no network.
- All dimensions are in millimetres unless the user says otherwise.
- Start with a `# PLAN:` comment block listing each feature of the part and where it sits in X/Y/Z. Complex shapes are usually several simple solids joined with .union() or removed with .cut(), positioned with .translate() — not one box.
- Declare every key dimension as a named UPPER_CASE variable, with a short comment, so the part is easy to edit parametrically.
- Declare `EXPECTED_BBOX = (x, y, z)`: the true overall extents of the finished part along X, Y and Z, computed from your dimension variables (e.g. a hexagon's corner-to-corner size is across_flats / cos(30°)). It is checked against the real geometry.
- Orientation: a hole or bore "horizontally through" a part runs along X or Y — build its cutter on the "YZ" plane (extrudes along +X) or the "XZ" plane (extrudes along -Y), not on "XY". Place hole patterns symmetrically about the part centre unless told otherwise.
- The final solid MUST be assigned to a variable named `result` (a `cq.Workplane` or `cq.Shape`).
- Do not call show_object, exporters, or print.
- Through holes and bores: the cutter must start below the part and extend past it (e.g. `workplane(offset=-1)` then `extrude(LENGTH + 2)`), never start part-way through.
- Holes and pockets: prefer cutting explicit solids (e.g. `cq.Workplane("XY").pushPoints(pts).circle(r).extrude(h)`) over chained workplane selectors. Hollow parts must actually be hollowed (cut an inner solid, or use .shell()).
- Apply fillets/chamfers last, only on edges that clearly exist. If a fillet is risky, use a smaller radius.
- Use standard engineering values where the user is vague and note assumptions in comments.
- Only use real CadQuery API: Workplane, box, cylinder, sphere, circle, rect, polygon, polyline, close, extrude, revolve, sweep, loft, hole, cboreHole, cskHole, pushPoints, rarray, polarArray, faces, edges, vertices, workplane, fillet, chamfer, shell, union, cut, intersect, translate, rotate, mirror. Box accepts `centered=(bool, bool, bool)`; `workplane()` accepts only `offset`, `invert`, `centerOption`, `origin`. `polygon(nSides, diameter, circumscribed=False)`: `diameter` is corner-to-corner; pass `circumscribed=True` to make it the across-flats size.
- ISO 273 medium clearance holes: M3 3.4, M4 4.5, M5 5.5, M6 6.6, M8 9.0, M10 11.0, M12 13.5 mm.

The examples below show technique only. Do not copy their shapes — model exactly what the user describes.

Example A — "flanged bushing: 20 mm bore, 30 mm body OD, 25 mm long, 50 mm flange OD 6 mm thick, three M5 holes on a 40 mm PCD":
```python
import math
import cadquery as cq

# PLAN:
# - flange disc at Z=0..6, centred on the Z axis
# - body cylinder on top of the flange, Z=6..25
# - bore through everything along Z
# - 3 holes through the flange on a 40 mm pitch circle

BORE_D = 20.0        # through bore
BODY_D = 30.0        # body outer diameter
TOTAL_L = 25.0       # overall length (Z)
FLANGE_D = 50.0      # flange outer diameter
FLANGE_T = 6.0       # flange thickness
PCD = 40.0           # bolt pitch circle diameter
BOLT_HOLE_D = 5.5    # M5 clearance hole

EXPECTED_BBOX = (FLANGE_D, FLANGE_D, TOTAL_L)

flange = cq.Workplane("XY").circle(FLANGE_D / 2).extrude(FLANGE_T)
body = cq.Workplane("XY").workplane(offset=FLANGE_T).circle(BODY_D / 2).extrude(TOTAL_L - FLANGE_T)
bore = cq.Workplane("XY").workplane(offset=-1).circle(BORE_D / 2).extrude(TOTAL_L + 2)
bolt_points = [(PCD / 2 * math.cos(math.radians(a)), PCD / 2 * math.sin(math.radians(a))) for a in (0, 120, 240)]
bolt_holes = cq.Workplane("XY").workplane(offset=-1).pushPoints(bolt_points).circle(BOLT_HOLE_D / 2).extrude(FLANGE_T + 2)

result = flange.union(body).cut(bore).cut(bolt_holes)
```

Example B — "150 x 100 x 12 mm base plate with a 40 x 40 x 60 mm upright block standing on its centre":
```python
import cadquery as cq

# PLAN:
# - base plate lying flat, Z=0..12, centred on the origin in X/Y
# - upright block sitting on top of the plate, Z=12..72, centred in X/Y
# - join the two solids

BASE_L = 150.0   # base length (X)
BASE_W = 100.0   # base width (Y)
BASE_T = 12.0    # base thickness (Z)
BLOCK_S = 40.0   # upright block section (X and Y)
BLOCK_H = 60.0   # upright block height (Z)

EXPECTED_BBOX = (BASE_L, BASE_W, BASE_T + BLOCK_H)

base = cq.Workplane("XY").box(BASE_L, BASE_W, BASE_T, centered=(True, True, False))
block = cq.Workplane("XY").box(BLOCK_S, BLOCK_S, BLOCK_H, centered=(True, True, False)).translate((0, 0, BASE_T))
result = base.union(block)
```

Reply with a one- or two-sentence summary of the design and its key assumptions, followed by the complete script in a single ```python code block."""

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "template": {"type": "string", "enum": [*templates.TEMPLATES, "custom"]},
        "feature_mapping": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"feature": {"type": "string"}, "parameter": {"type": ["string", "null"]}},
                "required": ["feature", "parameter"],
            },
        },
        "params": {"type": "object"},
        "summary": {"type": "string"},
    },
    "required": ["template", "feature_mapping", "params", "summary"],
}


# ---------------------------------------------------------------- backends

def _chat_ollama(system: str, messages: list[dict], model: str, json_schema: dict | None) -> str:
    import ollama

    client = ollama.Client(host=OLLAMA_HOST)
    try:
        response = client.chat(
            model=model,
            messages=[{"role": "system", "content": system}, *messages],
            format=json_schema,
            options={"temperature": 0.2, "num_ctx": 16384},
        )
    except ollama.ResponseError as e:
        if e.status_code == 404:
            raise GenerationError(f"Ollama model '{model}' is not installed. Run: ollama pull {model}") from e
        raise GenerationError(f"Ollama error: {e.error}") from e
    except ConnectionError as e:
        raise GenerationError(f"Cannot reach Ollama at {OLLAMA_HOST}. Is Ollama running?") from e
    return response.message.content


def _chat_claude(system: str, messages: list[dict]) -> str:
    import anthropic

    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=64000,
        system=system,
        messages=messages,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        # Server-side refusal fallback: re-runs a declined request on Anthropic's recommended model.
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise GenerationError("Claude declined this request.")
    if response.stop_reason == "max_tokens":
        raise GenerationError("Claude's reply was cut off (max_tokens reached).")
    return "".join(block.text for block in response.content if block.type == "text")


def chat(system: str, messages: list[dict], backend: str, model: str, json_schema: dict | None = None) -> str:
    if backend == "ollama":
        return _chat_ollama(system, messages, model, json_schema)
    if backend == "claude":
        return _chat_claude(system, messages)
    raise ValueError(f"Unknown backend: {backend}")


# ---------------------------------------------------------------- tasks

def choose_template(messages: list[dict], backend: str = DEFAULT_BACKEND, model: str = OLLAMA_MODEL) -> tuple[str, dict]:
    """Returns (raw reply, parsed {"template", "params", "summary"})."""
    text = chat(ROUTER_PROMPT, messages, backend, model, json_schema=ROUTER_SCHEMA)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match.group(0) if match else text)
    except json.JSONDecodeError as e:
        raise GenerationError(f"The model did not return valid JSON: {text[:300]}") from e
    if not isinstance(data, dict) or "template" not in data:
        raise GenerationError(f"The model's JSON is missing 'template': {text[:300]}")
    data.setdefault("params", {})
    data.setdefault("summary", "")
    return text, data


VERIFY_PROMPT = """You compare a customer's request for a mechanical part with a description of the part that was built.

Split the request into its individual requested features (overall size, each set of holes, each bore, fillet, keyway, hub, ...). For each one:
- "kind": the kind of feature.
- "evidence": copy, word for word, the sentence or phrase from the built-part description that provides this feature, or "" if no phrase does.
- "status": "ok" if the evidence provides the feature as requested, "missing" if nothing in the description provides it, "different" only if the description clearly states a different value, count or direction.

Only list features the customer actually asked for. Thread sizes are satisfied by ISO clearance holes: M3 = 3.4 mm, M4 = 4.5, M5 = 5.5, M6 = 6.6, M8 = 9, M10 = 11, M12 = 13.5, M16 = 17.5, M20 = 22 mm.
Reply with JSON only."""

VERIFY_KINDS = ["part type or overall size", "hole or bore", "fillet, chamfer or radius", "keyway, slot or pocket",
                "hub, boss or protrusion", "other feature"]

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requested": {"type": "string"},
                    "kind": {"type": "string", "enum": VERIFY_KINDS},
                    "evidence": {"type": "string"},
                    "status": {"type": "string", "enum": ["ok", "missing", "different"]},
                },
                "required": ["requested", "kind", "evidence", "status"],
            },
        },
    },
    "required": ["checks"],
}


def verify_part(requests: list[str], description: str, backend: str = DEFAULT_BACKEND, model: str = OLLAMA_MODEL) -> list[str]:
    """Independent check (no template catalogue in context): what does the built part lack?"""
    if len(requests) == 1:
        asked = requests[0]
    else:
        asked = "Original request, followed by later changes (later ones override earlier ones):\n" + \
                "\n".join(f"{i}. {r}" for i, r in enumerate(requests, 1))
    content = f"Customer asked for:\n{asked}\n\nPart that was built:\n{description}"
    text = chat(VERIFY_PROMPT, [{"role": "user", "content": content}], backend, model, json_schema=VERIFY_SCHEMA)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        checks = json.loads(match.group(0) if match else text).get("checks", [])
    except (json.JSONDecodeError, AttributeError):
        return []  # an unreadable verdict shouldn't block a part that built fine

    def norm(s):
        return re.sub(r"\s+", " ", s).strip().lower()

    def thread_sizes_satisfied(requested, evidence):
        pattern = r"\bM\d+(?:\.\d+)?\b"
        # The checker often shortens "four M16 bolt holes" to "bolt holes"; then use sizes from the whole request.
        sizes = re.findall(pattern, requested, re.IGNORECASE) or re.findall(pattern, asked, re.IGNORECASE)
        return bool(sizes) and all(
            re.search(rf"(?<![\d.]){templates.CLEARANCE_HOLES.get(s.upper(), -1):g}(?![\d.])", evidence) for s in sizes)

    described = norm(description)
    missing = []
    for c in checks:
        if not isinstance(c, dict):
            continue
        requested, evidence, status = str(c.get("requested", "")), norm(str(c.get("evidence", ""))), c.get("status")
        grounded = len(evidence) >= 8 and evidence in described  # the quote must really be in the description
        if status == "missing" and not grounded:
            # The chosen template already fixes the part type; names like "DN50 style flange" aren't features.
            if c.get("kind") != VERIFY_KINDS[0]:
                missing.append(f"{requested} (not in built part)")
        elif status == "different" and grounded and not thread_sizes_satisfied(requested, evidence):
            missing.append(f"{requested} (built: {c.get('evidence')})")
    return missing


def generate_code(messages: list[dict], backend: str = DEFAULT_BACKEND, model: str = OLLAMA_MODEL) -> tuple[str, str]:
    """Returns (full reply text, extracted code)."""
    text = chat(CODE_PROMPT, messages, backend, model)
    match = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return text, match.group(1).strip()
    # Unfenced reply: take everything from the first import onwards.
    start = re.search(r"^(import|from) \w+", text, re.MULTILINE)
    if start and "result" in text[start.start():]:
        return text, text[start.start():].strip().rstrip("`").strip()
    return text, ""
