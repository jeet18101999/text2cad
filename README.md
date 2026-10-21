# Text2CAD — words → engineering 3D models

A Windows desktop application. Describe a part in plain English; a **local AI model** (Ollama) turns it into a
parametric [CadQuery](https://cadquery.readthedocs.io) model, built by the OpenCascade CAD kernel and exported as
**STEP** (CAD/CNC) or **STL** (3D printing). Everything runs on your PC — no cloud, no browser.

## Install (one step)

Double-click **`install.bat`**. It will:

1. find Python 3.10–3.13 (or install Python 3.12 via winget),
2. find Ollama (or install it via winget) and start it,
3. pick an AI model for your hardware (`qwen3-coder:30b` with ≥ 28 GB RAM+VRAM, else `qwen2.5-coder:7b`) and download it,
4. create `.venv` and install the Python packages,
5. run a self-test that builds every part template,
6. add **Text2CAD** to your desktop and Start menu.

Re-running `install.bat` is safe (it updates/repairs). To force a model: `install.bat -Model qwen2.5-coder:7b`.

## Use

Open **Text2CAD** from the desktop or Start menu (or double-click `Text2CAD.bat`).

- Type a description and press **Generate** (Ctrl+Enter). Then keep refining: *"make the holes M8 and add 6 mm corner radii"*.
- **Templates** menu: start from a tested part (plate, L-bracket, flange, shaft, spur gear, enclosure, standoff, structural profile) without AI.
- **Parameters** panel: edit any template dimension and rebuild instantly — no AI involved.
- **3D view**: left-drag rotate, wheel zoom, middle-drag pan; **View** menu for iso/top/front/side (Ctrl+0…3).
- **File → Save STEP / STL / code**. The code is a standalone CadQuery script you can edit in CQ-Editor.
- **AI** menu: switch model, retry count, or use the Claude API instead (needs `ANTHROPIC_API_KEY` in `.env`).

Command line is also available:

```bash
.venv\Scripts\python cli.py "150 x 80 x 8 mm plate with six M5 holes 10 mm from the edges"
```

## How it works

```
description ─► AI picks a template + fills parameters ─► build ─► independent AI check: anything missing?
                     │ no template fits                                   │ yes
                     ▼                                                    ▼
              AI writes CadQuery code ─► build in subprocess ─► size check ─► errors fed back ─► retry
```

1. **Templates first.** Tested generators (`text2cad/templates.py`) cover common parts. The AI only chooses one and
   fills in numbers; thread sizes like `"M6"` are converted to ISO 273 clearance diameters in code, not by the AI.
2. **Independent check.** Code writes a plain-English description of what was actually built; a separate AI call
   compares it with the request. If anything is missing (e.g. a cross bore), the part goes to step 3.
3. **Custom code fallback.** The AI writes CadQuery code, run in a separate process. Errors (with the correct API
   signature when a function was misused) and gross size mismatches are fed back for up to N repair attempts.

## Project layout

| Path | Role |
|---|---|
| `text2cad/gui.py` | Desktop application (Qt + VTK 3D viewer) |
| `text2cad/pipeline.py` | Template → check → custom-code pipeline, self-repair loop |
| `text2cad/generator.py` | Prompts and calls to Ollama / Claude |
| `text2cad/templates.py` | Parametric part templates, parameter validation, descriptions |
| `text2cad/runner.py` | Builds a script in a child process, validates, exports STEP/STL |
| `cli.py` | Command-line interface |
| `scripts/setup.ps1` | Installer (run via `install.bat`) |
| `tests/test_templates.py` | Builds every template and checks volumes |
| `tests/test_gui.py` | Desktop app smoke test |
| `outputs/` | Every generated part (`model.step`, `model.stl`, `part.py`) |

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `TEXT2CAD_BACKEND` | `ollama` | `ollama` (local) or `claude` |
| `OLLAMA_MODEL` | set by installer | Any Ollama model; coding models work best |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama server |
| `ANTHROPIC_API_KEY` | — | Only for the Claude backend |

## Limits — check before you manufacture

- Template parts are reliable. **Custom-code parts depend on the AI model** and can be wrong in ways the automatic
  checks miss (a hole in the wrong place, a feature slightly off). Always inspect the 3D view and dimensions.
- Generated code runs in a separate process with a timeout, but it is **not a security sandbox**.
- Local model speed: `qwen3-coder:30b` takes ~15–60 s per part on an 8 GB GPU laptop; the first request after start
  is slower while the model loads.

## Licence

MIT. See [LICENSE](LICENSE).
