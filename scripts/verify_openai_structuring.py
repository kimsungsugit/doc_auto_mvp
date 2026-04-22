from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import DocumentField, ValidationStatus
from app.services.ai_structurer import OpenAIStructurer


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Install extras and set the key to run live verification.")
        return

    os.environ["OPENAI_STRUCTURING_ENABLED"] = "true"
    structurer = OpenAIStructurer()
    fields = [
        DocumentField(field_name="supplier_name", label="공급자명", value="", validation_status=ValidationStatus.MISSING),
        DocumentField(field_name="buyer_name", label="공급받는자명", value="", validation_status=ValidationStatus.MISSING),
        DocumentField(field_name="tax_amount", label="세액", value="", validation_status=ValidationStatus.MISSING),
    ]
    text = "\n".join(
        [
            "세금계산서",
            "작성일자 2026-04-11",
            "공급자 301-11-11111 OCR상사",
            "공급받는자 401-22-22222 OCR고객",
            "공급가액 500,000",
            "세액 50,000",
            "합계 550,000",
        ]
    )
    result = structurer.maybe_refine(text, fields)
    payload = {field.field_name: field.value for field in result}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
