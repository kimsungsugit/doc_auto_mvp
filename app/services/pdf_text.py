from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass
class TextExtractionResult:
    text: str
    requires_ocr: bool
    warnings: list[str]


class PdfTextService:
    def extract(self, pdf_path: Path) -> TextExtractionResult:
        reader = PdfReader(str(pdf_path))
        pages = [(page.extract_text() or "") for page in reader.pages]
        text = "\n".join(pages).strip()
        if text:
            return TextExtractionResult(text=text, requires_ocr=False, warnings=[])
        return TextExtractionResult(
            text="",
            requires_ocr=True,
            warnings=["텍스트를 직접 추출하지 못했습니다. OCR 엔진이 필요합니다."],
        )
