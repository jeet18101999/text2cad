"""Text -> part pipeline.

1. Router: the LLM picks a tested template and fills in its parameters (reliable, even for small models).
2. Fallback: if no template fits, the LLM writes free-form CadQuery code.
Either way the part is built in a subprocess and any error is fed back to the LLM to fix.
"""

import json
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import templates
from .generator import DEFAULT_BACKEND, OLLAMA_MODEL, GenerationError, choose_template, generate_code, verify_part

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = PROJECT_ROOT / "outputs"
EXEC_TIMEOUT_S = 180


@dataclass
class Attempt:
    mode: str  # "template" or "code"
    detail: str  # template name or first line of code summary
    error: str | None = None


@dataclass
class BuildResult:
    ok: bool
    output_dir: Path
    mode: str = ""  # "template" or "code"
    template: str | None = None
    params: dict = field(default_factory=dict)
    code: str = ""
    summary: str = ""
    stats: dict = field(default_factory=dict)
    attempts: list[Attempt] = field(default_factory=list)
    error: str | None = None


def _new_output_dir() -> Path:
    return OUTPUT_ROOT / uuid.uuid4().hex[:8]


def execute(code: str, out_dir: Path, check_bbox: bool = True) -> dict:
    """Run a CadQuery script in a separate process so crashes/hangs can't take down the app."""
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / "part.py"
    script.write_text(code, encoding="utf-8")
    # The desktop app runs under pythonw.exe (no console); use python.exe for the child so stdout
    # capture works, and hide its console window.
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")
    cmd = [str(python), "-m", "text2cad.runner", str(script), str(out_dir)]
    if not check_bbox:
        cmd.append("--no-bbox-check")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=EXEC_TIMEOUT_S, cwd=PROJECT_ROOT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Execution timed out after {EXEC_TIMEOUT_S}s (geometry too complex?)."}
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {"ok": False, "error": f"Runner crashed (exit {proc.returncode}): {proc.stderr.strip()[-1500:]}"}


def build_template(name: str, params: dict, summary: str = "", out_dir: Path | None = None) -> BuildResult:
    """Build a template part directly (no AI)."""
    out_dir = out_dir or _new_output_dir()
    result = BuildResult(ok=False, output_dir=out_dir, mode="template", template=name, summary=summary)
    try:
        result.params = templates.coerce_params(name, params)
    except ValueError as e:
        result.error = str(e)
        return result
    result.code = templates.standalone_script(name, result.params)
    run = execute(result.code, out_dir, check_bbox=False)
    result.ok = run["ok"]
    result.error = None if run["ok"] else run["error"]
    result.stats = {k: v for k, v in run.items() if k not in ("ok", "error", "traceback")}
    return result


def _build_code(request: str, current: "BuildResult | None", max_attempts: int, backend: str, model: str,
                notify, attempts: list[Attempt], summary_hint: str = "") -> BuildResult:
    out_dir = _new_output_dir()
    result = BuildResult(ok=False, output_dir=out_dir, mode="code", attempts=attempts)
    if current is not None and current.ok and current.code:
        first = (f"Here is the current CadQuery script for the part:\n\n```python\n{current.code}\n```\n\n"
                 f"Modify it as follows (keep EXPECTED_BBOX and `result`): {request}")
    else:
        first = request
    messages = [{"role": "user", "content": first}]

    for i in range(1, max_attempts + 1):
        notify(f"Writing custom CAD code (attempt {i}/{max_attempts})...")
        try:
            reply, code = generate_code(messages, backend=backend, model=model)
        except GenerationError as e:
            result.error = str(e)
            return result
        messages.append({"role": "assistant", "content": reply})
        if not code:
            attempts.append(Attempt("code", "custom code", "Reply contained no code."))
            result.error = "The model did not return a script."
            messages.append({"role": "user", "content": "Reply with the complete script in a single ```python code block."})
            continue

        notify("Building geometry...")
        run = execute(code, out_dir)
        result.code = code
        result.summary = reply.split("```")[0].strip() or summary_hint
        attempts.append(Attempt("code", "custom code", None if run["ok"] else run["error"]))
        if run["ok"]:
            result.ok, result.error = True, None
            result.stats = {k: v for k, v in run.items() if k != "ok"}
            return result
        result.error = run["error"]
        messages.append({
            "role": "user",
            "content": (
                f"That script failed:\n\n{run['error']}\n{run.get('traceback', '')}\n\n"
                "Fix the problem and reply with the complete corrected script in a single ```python block."
            ),
        })
    return result


def build(
    request: str,
    current: BuildResult | None = None,
    max_attempts: int = 4,
    backend: str = DEFAULT_BACKEND,
    model: str = OLLAMA_MODEL,
    on_progress=None,
    history: list[str] | None = None,
    verify: bool = True,
) -> BuildResult:
    """Turn a text request into a part.

    If `current` is a previous successful result, the request modifies it; `history` holds the earlier
    requests that shaped it (used when checking the result). With `verify`, template parts are checked by an
    independent AI call against the request, and fall back to custom code if features are missing.
    """
    notify = on_progress or (lambda msg: None)
    attempts: list[Attempt] = []
    all_requests = [*(history or []), request] if current is not None else [request]

    # A part that is already custom code stays custom code.
    if current is not None and current.ok and current.mode == "code":
        return _build_code(request, current, max_attempts, backend, model, notify, attempts)

    if current is not None and current.ok and current.mode == "template":
        content = (f"Current design: template={current.template}, "
                   f"params={json.dumps(current.params)}\n\nRequest: {request}")
    else:
        content = request
    messages = [{"role": "user", "content": content}]
    last = BuildResult(ok=False, output_dir=_new_output_dir(), attempts=attempts)

    for i in range(1, max_attempts + 1):
        notify(f"Matching your description to a part template (attempt {i}/{max_attempts})...")
        try:
            reply, choice = choose_template(messages, backend=backend, model=model)
        except GenerationError as e:
            last.error = str(e)
            return last

        unsupported = []
        if choice["template"] in templates.TEMPLATES:
            valid = set(templates.parameters(choice["template"])) | {"shape"}
            mapping = [m for m in choice.get("feature_mapping") or [] if isinstance(m, dict)]
            unsupported = [m.get("feature", "?") for m in mapping if m.get("parameter") not in valid]
            # One parameter can't produce two different features (e.g. hole_diameter for both an
            # M3 pattern and M5 corner holes) - that means the template doesn't really fit.
            claimed: dict[str, set] = {}
            for m in mapping:
                if m.get("parameter") in valid and m["parameter"] != "shape":
                    claimed.setdefault(m["parameter"], set()).add(str(m.get("feature", "")).strip().lower())
            unsupported += [f"{p} needed for: {' / '.join(sorted(f))}" for p, f in claimed.items() if len(f) > 1]
        if choice["template"] == "custom" or unsupported:
            reason = f" (not covered: {', '.join(unsupported)})" if unsupported else ""
            notify(f"No template fits this part{reason} — switching to custom code.")
            return _build_code(request, current, max_attempts, backend, model, notify, attempts, choice["summary"])

        notify(f"Building '{choice['template']}' template...")
        last = build_template(choice["template"], choice["params"], choice["summary"])
        attempts.append(Attempt("template", choice["template"], last.error))
        last.attempts = attempts
        if last.ok and verify:
            notify("Checking the part against your description...")
            try:
                missing = verify_part(all_requests, templates.describe(last.template, last.params), backend=backend, model=model)
            except GenerationError as e:
                notify(f"Check skipped: {e}")
                missing = []
            if missing:
                attempts[-1].error = "template result is missing: " + "; ".join(missing)
                notify(f"Template result is missing: {'; '.join(missing)} — switching to custom code.")
                return _build_code(request, current, max_attempts, backend, model, notify, attempts, choice["summary"])
        if last.ok:
            return last

        messages.append({"role": "assistant", "content": reply})
        messages.append({
            "role": "user",
            "content": (f"Building that failed: {last.error}\n\nAdjust the parameters so the part is valid, "
                        "or choose \"custom\" if this template cannot represent the part. Reply with JSON only."),
        })
    return last
