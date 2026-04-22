from __future__ import annotations

import json
import sys
import asyncio
from pathlib import Path

from app.services.ocr import OcrService, OcrUnavailableError

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import system_readiness


def main() -> None:
    payload = asyncio.run(system_readiness())
    result = payload.model_dump(mode="json")
    result["ocr_probe"] = build_ocr_probe()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_ocr_probe() -> dict[str, object]:
    service = OcrService()
    probe: dict[str, object] = {"default_lang": service.default_lang}
    try:
        probe["tesseract_path"] = service._resolve_tesseract_path()
        probe["tesseract_ready"] = True
    except OcrUnavailableError as error:
        probe["tesseract_ready"] = False
        probe["tesseract_error"] = str(error)

    try:
        probe["ghostscript_path"] = service._resolve_ghostscript_path()
        probe["ghostscript_ready"] = True
    except OcrUnavailableError as error:
        probe["ghostscript_ready"] = False
        probe["ghostscript_error"] = str(error)

    return probe


if __name__ == "__main__":
    main()
