from __future__ import annotations

import json
import sys
from pathlib import Path

from app.services.ocr import OcrService, OcrUnavailableError


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples") / "generated" / "sample_24_scan_style_invoice.pdf"
    service = OcrService()

    result: dict[str, object] = {
        "target": str(target),
        "exists": target.exists(),
        "default_lang": service.default_lang,
    }

    try:
        result["tesseract_path"] = service._resolve_tesseract_path()
        result["tesseract_ready"] = True
    except OcrUnavailableError as error:
        result["tesseract_ready"] = False
        result["tesseract_error"] = str(error)

    try:
        result["ghostscript_path"] = service._resolve_ghostscript_path()
        result["ghostscript_ready"] = True
    except OcrUnavailableError as error:
        result["ghostscript_ready"] = False
        result["ghostscript_error"] = str(error)

    if target.exists() and result.get("tesseract_ready"):
        try:
            if target.suffix.lower() == ".pdf":
                text = service.extract(target)
            else:
                text = service.extract_image(target)
            result["ocr_success"] = True
            result["preview"] = text[:500]
        except OcrUnavailableError as error:
            result["ocr_success"] = False
            result["ocr_error"] = str(error)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
