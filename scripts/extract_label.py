from __future__ import annotations

import asyncio
import argparse
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.verification.vision import (
    ImagePreprocessingError,
    OpenAIVisionService,
    VisionConfigurationError,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract TTB label fields from one image.")
    parser.add_argument("image_path", type=Path, help="Path to a local label image.")
    args = parser.parse_args()

    image_path = args.image_path
    if not image_path.exists() or not image_path.is_file():
        print(f"Image file not found: {image_path}", file=sys.stderr)
        return 2

    try:
        service = OpenAIVisionService.from_env()
    except VisionConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        label = asyncio.run(service.extract_label(image_path.read_bytes(), _content_type_for(image_path)))
    except ImagePreprocessingError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    payload = label.model_dump()
    print(json.dumps(payload, indent=2))
    if all(value is None for value in payload.values()):
        print("Extraction returned no populated fields.", file=sys.stderr)
        return 1
    return 0


def _content_type_for(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return None


if __name__ == "__main__":
    raise SystemExit(main())
