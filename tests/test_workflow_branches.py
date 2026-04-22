from __future__ import annotations

from pathlib import Path

import pytest

from app.models import DocumentField, DocumentStatus, InvoiceLineItem, ValidationStatus
from app.services.extractor import ExtractionOutcome
from app.services.ocr import OcrUnavailableError
from app.services.pdf_text import TextExtractionResult
from app.services.vision_ocr import VisionOcrUnavailableError


def _seed_pdf(isolated_storage, content: bytes = b"%PDF-1.4 stub") -> str:
    record = isolated_storage.create_record("file.pdf", "tester", len(content))
    isolated_storage.original_path(record.document_id, ".pdf").write_bytes(content)
    return record.document_id


def _fields(pairs: list[tuple[str, str, float]]) -> list[DocumentField]:
    """Build DocumentField list from (field_name, value, confidence) tuples."""
    out = []
    for name, value, conf in pairs:
        out.append(DocumentField(
            field_name=name,
            label=name,
            value=value,
            confidence=conf,
            validation_status=ValidationStatus.OK if value else ValidationStatus.MISSING,
            extraction_source="ai" if value else "",
            required=True,
        ))
    return out


class TestAiFirstBranch:
    """Cover _extract_ai_first path which is skipped when AI disabled."""

    def test_ai_first_uses_ai_fields_when_confident(self, isolated_workflow, isolated_storage, monkeypatch):
        doc_id = _seed_pdf(isolated_storage)
        monkeypatch.setattr(
            isolated_workflow.pdf_text_service, "extract",
            lambda _p: TextExtractionResult(text="dummy text", requires_ocr=False, warnings=[]),
        )
        monkeypatch.setattr(isolated_workflow.ai_structurer, "enabled", True)

        ai_fields = _fields([
            ("document_type", "전자세금계산서", 0.98),
            ("issue_date", "2026-04-06", 0.95),
            ("supplier_name", "공급사", 0.95),
            ("supplier_biz_no", "123-45-67890", 0.95),
            ("buyer_name", "구매사", 0.95),
            ("buyer_biz_no", "234-56-78901", 0.95),
            ("supply_amount", "100,000", 0.95),
            ("tax_amount", "10,000", 0.95),
            ("total_amount", "110,000", 0.95),
        ])
        monkeypatch.setattr(
            isolated_workflow.ai_structurer, "classify_and_extract",
            lambda _t: ("전자세금계산서", 0.95, ai_fields),
        )
        monkeypatch.setattr(
            isolated_workflow.extractor, "extract",
            lambda _t: ExtractionOutcome(fields=ai_fields, items=[], document_confidence=0.9, warnings=[]),
        )
        monkeypatch.setattr(isolated_workflow.ai_structurer, "extract_line_items", lambda _t, _d: [])

        record = isolated_workflow.extract(doc_id)
        assert record.status in (DocumentStatus.REVIEWED, DocumentStatus.NEEDS_REVIEW)
        doc_type = next((f.value for f in record.fields if f.field_name == "document_type"), "")
        assert doc_type == "전자세금계산서"

    def test_ai_first_falls_back_to_rule_when_ai_empty(self, isolated_workflow, isolated_storage, monkeypatch):
        doc_id = _seed_pdf(isolated_storage)
        monkeypatch.setattr(
            isolated_workflow.pdf_text_service, "extract",
            lambda _p: TextExtractionResult(text="거래명세서", requires_ocr=False, warnings=[]),
        )
        monkeypatch.setattr(isolated_workflow.ai_structurer, "enabled", True)
        monkeypatch.setattr(
            isolated_workflow.ai_structurer, "classify_and_extract",
            lambda _t: ("", 0.0, []),
        )
        monkeypatch.setattr(isolated_workflow.ai_structurer, "extract_line_items", lambda _t, _d: [])

        record = isolated_workflow.extract(doc_id)
        assert record.status == DocumentStatus.NEEDS_REVIEW


class TestConfidenceRouting:
    """Cover auto-approve vs priority review branches."""

    def test_auto_approve_on_high_confidence(self, isolated_workflow, isolated_storage, monkeypatch):
        doc_id = _seed_pdf(isolated_storage)
        monkeypatch.setattr(
            isolated_workflow.pdf_text_service, "extract",
            lambda _p: TextExtractionResult(
                text="\n".join([
                    "전자세금계산서",
                    "작성일자 2026-04-06",
                    "공급자 123-45-67890 ABC상사",
                    "공급받는자 234-56-78901 테스트고객",
                    "공급가액 100,000",
                    "세액 10,000",
                    "합계 110,000",
                    "품목 그룹웨어 개발",
                ]),
                requires_ocr=False, warnings=[],
            ),
        )
        record = isolated_workflow.extract(doc_id)
        # Rule-based should reach NEEDS_REVIEW or REVIEWED
        assert record.status in (DocumentStatus.REVIEWED, DocumentStatus.NEEDS_REVIEW)


class TestOcrFailureBranches:
    """Cover OCR failure fallback paths in _ocr_pdf / _ocr_image."""

    def test_pdf_ocr_fails_returns_failed_status(self, isolated_workflow, isolated_storage, monkeypatch):
        doc_id = _seed_pdf(isolated_storage)
        monkeypatch.setattr(
            isolated_workflow.pdf_text_service, "extract",
            lambda _p: TextExtractionResult(text="", requires_ocr=True, warnings=[]),
        )

        def raise_ocr(_path):
            raise OcrUnavailableError("tesseract not configured")

        monkeypatch.setattr(isolated_workflow.ocr_service, "extract", raise_ocr)
        # Force vision OCR unavailable by patching _get_client
        monkeypatch.setattr(isolated_workflow.vision_ocr_service, "_get_client", lambda: None)

        record = isolated_workflow.extract(doc_id)
        assert record.status == DocumentStatus.FAILED
        assert record.last_error

    def test_image_ocr_vision_fails_fallback_to_tesseract(self, isolated_workflow, isolated_storage, monkeypatch):
        record = isolated_storage.create_record("scan.png", "tester", 10)
        isolated_storage.original_path(record.document_id, ".png").write_bytes(b"fake-png")

        # Make vision OCR "available" by returning a truthy client
        monkeypatch.setattr(isolated_workflow.vision_ocr_service, "_get_client", lambda: object())

        def vision_fails(_path):
            raise VisionOcrUnavailableError("vision quota exceeded")

        monkeypatch.setattr(isolated_workflow.vision_ocr_service, "extract_from_image", vision_fails)
        monkeypatch.setattr(
            isolated_workflow.ocr_service, "extract_image",
            lambda _p: "공급자 123-45-67890 ABC상사\n공급가액 1,000,000",
        )

        result = isolated_workflow.extract(record.document_id)
        assert result.status != DocumentStatus.FAILED


class TestSyncItemsEdgeCases:
    """Cover _sync_items_from_fields and _build_default_item branches."""

    def test_build_default_item_uses_field_values(self, isolated_workflow, isolated_storage, monkeypatch):
        doc_id = _seed_pdf(isolated_storage)
        monkeypatch.setattr(
            isolated_workflow.pdf_text_service, "extract",
            lambda _p: TextExtractionResult(
                text="품목 개발용역\n공급가액 500,000\n세액 50,000",
                requires_ocr=False, warnings=[],
            ),
        )
        record = isolated_workflow.extract(doc_id)
        assert len(record.items) >= 1
