"""Command-line Text2CAD.

Usage:
  python cli.py "100 x 60 x 10 mm plate with four M6 holes"
  python cli.py --backend claude "flanged bushing, 20 mm bore, 40 mm OD flange"
"""

import argparse

from dotenv import load_dotenv

load_dotenv()

from text2cad.generator import DEFAULT_BACKEND, OLLAMA_MODEL  # noqa: E402
from text2cad.pipeline import build  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Turn a text description into a CAD model (STEP/STL).")
    parser.add_argument("description")
    parser.add_argument("--backend", choices=["ollama", "claude"], default=DEFAULT_BACKEND)
    parser.add_argument("--model", default=OLLAMA_MODEL, help="Ollama model name")
    parser.add_argument("--attempts", type=int, default=4)
    args = parser.parse_args()

    result = build(args.description, max_attempts=args.attempts, backend=args.backend, model=args.model, on_progress=print)
    if result.ok:
        how = f"template '{result.template}' {result.params}" if result.mode == "template" else "custom code"
        print(f"\n{result.summary}\nBuilt from: {how}")
        print(f"Bounding box: {result.stats['bbox_mm']} mm, volume: {result.stats['volume_mm3']} mm^3")
        print(f"Files: {result.output_dir}")
    else:
        print(f"\nFailed after {len(result.attempts)} attempt(s): {result.error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
