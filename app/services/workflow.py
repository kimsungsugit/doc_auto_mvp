from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from app.confidence_thresholds import (
    CONFIDENCE_AI_MARGIN_OVER_RULE,
    CONFIDENCE_AI_TRUSTED,
    CONFIDENCE_AUTO_APPROVE,
    CONFIDENCE_REVIEW_NORMAL,
    CONFIDENCE_RULE_TRUSTED,
)
from app.models import (
    DocumentField,
    DocumentRecord,
    DocumentStatus,
    EditHistoryEntry,
    InvoiceLineItem,
    ValidationStatus,
)
from app.schema import build_empty_fields
from app.services.ai_structurer import OpenAIStructurer
from app.services.exporter import ExcelExporter
from app.services.extractor import InvoiceFieldExtractor, classify_text
from app.services.feedback_collector import FeedbackCollector
from app.services.ocr import OcrService, OcrUnavailableError
from app.services.pdf_text import PdfTextService
from app.services.storage import StorageService
from app.services.vision_ocr import VisionOcrService, VisionOcrUnavailableError

logger = logging.getLogger(__name__)


class DocumentWorkflowService:
    def __init__(self, storage: StorageService) -> None:
        self.storage = storage
        self.pdf_text_service = PdfTextService()
        self.ocr_service = OcrService()
        self.vision_ocr_service = VisionOcrService()
        self.extractor = InvoiceFieldExtractor()
        self.ai_structurer = OpenAIStructurer()
        self.exporter = ExcelExporter()
        self.feedback_collector = FeedbackCollector(storage.base_dir)

    def extract(self, document_id: str, forced_type: str = "") -> DocumentRecord:
        """Extract fields. Pass `forced_type` to override classification (manual reclassification)."""
        record = self.storage.load_record(document_id)
        if record.extracted_at is not None or record.status == DocumentStatus.FAILED:
            record.retry_count += 1
        source_path = self.storage.original_path(document_id, record.original_extension)
        extracted_text = ""
        warnings: list[str] = []

        if record.original_extension == ".pdf":
            text_result = self.pdf_text_service.extract(source_path)
            record.requires_ocr = text_result.requires_ocr
            extracted_text = text_result.text
            warnings = list(text_result.warnings)

            if text_result.requires_ocr:
                extracted_text = self._ocr_pdf(document_id, source_path, warnings)
                if extracted_text is None:
                    record.status = DocumentStatus.FAILED
                    record.last_error = warnings[-1] if warnings else "OCR failed"
                    record.warnings = warnings
                    self.storage.save_record(record)
                    return record
        else:
            record.requires_ocr = True
            extracted_text = self._ocr_image(document_id, source_path, warnings)
            if extracted_text is None:
                record.status = DocumentStatus.FAILED
                record.last_error = warnings[-1] if warnings else "OCR failed"
                record.warnings = warnings
                self.storage.save_record(record)
                return record

        self.storage.ocr_path(document_id).write_text(extracted_text, encoding="utf-8")

        if self.ai_structurer.enabled:
            record = self._extract_ai_first(document_id, record, extracted_text, warnings, forced_type)
        else:
            record = self._extract_rule_based(document_id, record, extracted_text, warnings, forced_type)

        return record

    def _extract_ai_first(self, document_id: str, record: DocumentRecord, text: str, warnings: list[str], forced_type: str = "") -> DocumentRecord:
        """AI-First extraction: single API call for classify+extract, rule-based validates."""
        # Step 1: Combined AI classification + field extraction (single API call)
        ai_doc_type, ai_confidence, ai_fields = self.ai_structurer.classify_and_extract(text)
        self.storage.append_log(document_id, "info", f"AI classification: {ai_doc_type} (confidence: {ai_confidence:.2f})")

        # Step 2: Rule-based extraction for cross-validation / fallback.
        # classify_text를 별도 호출 — extract() 내부도 같은 함수를 쓰지만 여기서는 confidence를 활용하기 위함.
        rule_extraction = self.extractor.extract(text)
        rule_doc_type, rule_confidence = classify_text(text)
        self.storage.append_log(document_id, "info", f"Rule classification: {rule_doc_type} (confidence: {rule_confidence:.2f})")

        # Step 3: Decide document type — forced_type > weighted merge
        document_type = self._merge_classifications(
            document_id, forced_type, ai_doc_type, ai_confidence, rule_doc_type, rule_confidence, warnings,
        )

        # Step 4: Use AI fields or fall back to rule-based
        if ai_fields:
            record.fields = ai_fields
            # Cross-validate with rule-based results
            rule_fields = {f.field_name: f for f in rule_extraction.fields}
            for field in record.fields:
                rule_field = rule_fields.get(field.field_name)
                if not rule_field:
                    continue
                if field.value and rule_field.value and field.value != rule_field.value:
                    if field.field_name in ("supply_amount", "tax_amount", "total_amount", "supplier_biz_no", "buyer_biz_no"):
                        if rule_field.confidence > field.confidence:
                            field.validation_status = ValidationStatus.WARNING
                            warnings.append(f"{field.label}: AI({field.value}) ≠ 규칙({rule_field.value})")
                elif not field.value and rule_field.value:
                    field.value = rule_field.value
                    field.confidence = rule_field.confidence
                    field.extraction_source = "rule"
                    field.source_snippet = rule_field.source_snippet

            self.storage.append_log(document_id, "info", "AI field extraction completed")
        else:
            record.fields = rule_extraction.fields
            warnings.extend(rule_extraction.warnings)
            self.storage.append_log(document_id, "warning", "AI extraction failed, using rule-based fallback")

        # Step 4b: 강제 재분류된 경우 → 목표 유형 스키마로 필드 재구성 (유형 특화 필드 누락 방지)
        if forced_type:
            record.fields = self._reshape_fields_to_schema(record.fields, forced_type)
            for field in record.fields:
                if field.field_name == "document_type":
                    field.value = forced_type
                    field.confidence = 1.0
                    field.extraction_source = "manual"
                    break

        # Step 5: AI line items extraction (separate call, only if needed)
        ai_items = self.ai_structurer.extract_line_items(text, document_type)
        record.items = ai_items if ai_items else rule_extraction.items

        self._sync_items_from_fields(record)
        record.warnings = warnings
        record.extracted_at = datetime.now(UTC)
        record.last_error = None
        self._apply_confidence_routing(document_id, record)
        self._save_extraction_result(document_id, record, text)
        return record

    def _extract_rule_based(self, document_id: str, record: DocumentRecord, text: str, warnings: list[str], forced_type: str = "") -> DocumentRecord:
        """Rule-based extraction with optional AI refinement (legacy behavior)."""
        extraction = self.extractor.extract(text)
        extraction.fields = self.ai_structurer.maybe_refine(text, extraction.fields)
        record.fields = extraction.fields
        if forced_type:
            record.fields = self._reshape_fields_to_schema(record.fields, forced_type)
            for field in record.fields:
                if field.field_name == "document_type":
                    field.value = forced_type
                    field.confidence = 1.0
                    field.extraction_source = "manual"
            self.storage.append_log(document_id, "info", f"Document type manually forced: {forced_type}")
        record.items = extraction.items
        self._sync_items_from_fields(record)
        record.warnings = warnings + extraction.warnings
        record.extracted_at = datetime.now(UTC)
        record.last_error = None
        self._apply_confidence_routing(document_id, record)
        self.storage.append_log(document_id, "info", "Rule-based field extraction completed")
        self._save_extraction_result(document_id, record, text)
        return record

    def _apply_confidence_routing(self, document_id: str, record: DocumentRecord) -> None:
        """Route document based on extraction confidence: auto-approve, normal review, or priority review."""
        fields_by_name = record.fields_by_name
        confidences = [f.confidence for f in record.fields if f.value]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        missing_required = [f.label for f in record.fields if f.required and not f.value]
        amounts_valid = self._amounts_cross_check(fields_by_name)

        if avg_confidence >= CONFIDENCE_AUTO_APPROVE and not missing_required and amounts_valid:
            record.status = DocumentStatus.REVIEWED
            record.auto_approved = True
            record.reviewed_at = datetime.now(UTC)
            record.review_priority = "auto"
            self.storage.append_log(document_id, "info", f"Auto-approved (confidence: {avg_confidence:.2f})")
        elif avg_confidence < CONFIDENCE_REVIEW_NORMAL:
            record.status = DocumentStatus.NEEDS_REVIEW
            record.review_priority = "high"
            self.storage.append_log(document_id, "info", f"Priority review required (confidence: {avg_confidence:.2f})")
        else:
            record.status = DocumentStatus.NEEDS_REVIEW
            record.review_priority = "normal"

    def _amounts_cross_check(self, fields_by_name: dict[str, DocumentField]) -> bool:
        """Check if supply_amount + tax_amount == total_amount."""
        try:
            supply = fields_by_name.get("supply_amount")
            tax = fields_by_name.get("tax_amount")
            total = fields_by_name.get("total_amount")
            if not (supply and supply.value and tax and tax.value and total and total.value):
                return True  # Can't validate, assume ok
            s = int(supply.value.replace(",", ""))
            t = int(tax.value.replace(",", ""))
            tot = int(total.value.replace(",", ""))
            return s + t == tot
        except (ValueError, AttributeError):
            return False

    def _merge_classifications(
        self,
        document_id: str,
        forced_type: str,
        ai_type: str,
        ai_conf: float,
        rule_type: str,
        rule_conf: float,
        warnings: list[str],
    ) -> str:
        """Merge AI + rule classifications into final document type.

        Decision rules (first match wins):
          1) forced_type (manual override) → always wins.
          2) Both agree → that type.
          3) Only one has a type → that type (with confidence gating).
          4) Disagree → higher confidence wins; log mismatch as warning.
          5) Neither has anything → default 전자세금계산서 + warning.
        """
        if forced_type:
            self.storage.append_log(document_id, "info", f"Document type manually forced: {forced_type}")
            return forced_type

        if ai_type and rule_type and ai_type == rule_type:
            self.storage.append_log(document_id, "info", f"AI and rule agree on: {ai_type}")
            return ai_type

        if ai_type and not rule_type:
            self.storage.append_log(document_id, "info", f"Rule classifier empty, using AI: {ai_type}")
            return ai_type

        if rule_type and not ai_type:
            self.storage.append_log(document_id, "info", f"AI classifier empty, using rule: {rule_type}")
            return rule_type

        if ai_type and rule_type and ai_type != rule_type:
            # Weighted resolution: AI weight trusted above threshold, rule trusted when conf ≥ threshold
            if ai_conf >= CONFIDENCE_AI_TRUSTED and ai_conf >= rule_conf + CONFIDENCE_AI_MARGIN_OVER_RULE:
                winner = ai_type
                loser = rule_type
                reason = f"AI(conf {ai_conf:.2f}) > 규칙(conf {rule_conf:.2f})"
            elif rule_conf >= CONFIDENCE_RULE_TRUSTED and rule_conf >= ai_conf:
                winner = rule_type
                loser = ai_type
                reason = f"규칙(conf {rule_conf:.2f}) ≥ AI(conf {ai_conf:.2f})"
            else:
                winner = ai_type if ai_conf >= rule_conf else rule_type
                loser = rule_type if winner == ai_type else ai_type
                reason = f"둘 다 저신뢰(AI {ai_conf:.2f} / 규칙 {rule_conf:.2f}) → 높은 쪽 선택"
            warnings.append(f"문서 유형 불일치: AI={ai_type} / 규칙={rule_type} → {winner} 선택")
            self.storage.append_log(
                document_id, "warning",
                f"Classification mismatch resolved: kept={winner}, discarded={loser}, reason={reason}",
            )
            return winner

        # Neither matched
        warnings.append("문서 유형을 판별하지 못하여 기본값(전자세금계산서)을 사용합니다.")
        self.storage.append_log(document_id, "warning", "Document type unknown, defaulting to 전자세금계산서")
        return "전자세금계산서"

    def _reshape_fields_to_schema(self, current: list[DocumentField], target_type: str) -> list[DocumentField]:
        """현재 필드 목록을 `target_type` 스키마 필드 셋으로 재구성하면서 값 유지.

        - AI가 전자세금계산서 스키마로 뽑았는데 사용자가 "영수증"으로 강제 재분류한 경우:
          영수증 필드(approval_no 등)가 record에 누락되지 않도록 템플릿 생성 후
          name 매칭으로 값을 채운다. 타깃 스키마에 없는 필드는 제거.
        """
        template = build_empty_fields(target_type)
        by_name = {f.field_name: f for f in current}
        rebuilt: list[DocumentField] = []
        for slot in template:
            src = by_name.get(slot.field_name)
            if src is None:
                rebuilt.append(slot)
                continue
            # 기존 값 유지 + 라벨/required는 타깃 스키마 기준
            src.label = slot.label
            src.required = slot.required
            rebuilt.append(src)
        return rebuilt

    def _save_extraction_result(self, document_id: str, record: DocumentRecord, text: str) -> None:
        self.storage.save_raw_payload(
            document_id,
            {
                "document_id": document_id,
                "text": text,
                "fields": [field.model_dump(mode="json") for field in record.fields],
                "items": [item.model_dump(mode="json") for item in record.items],
                "warnings": record.warnings,
            },
        )
        self.storage.append_log(document_id, "info", "Field extraction completed")
        self.storage.save_record(record)

    def update_field(self, document_id: str, field_name: str, value: str, updated_by: str, comment: str | None) -> DocumentRecord:
        record = self.storage.load_record(document_id)
        matched = False
        for field in record.fields:
            if field.field_name != field_name:
                continue
            matched = True
            old_value = field.value
            old_source = field.extraction_source
            field.value = value
            field.updated_by = updated_by
            field.updated_at = datetime.now(UTC)
            field.extraction_source = "manual"
            self.storage.append_audit(
                document_id,
                EditHistoryEntry(
                    timestamp=field.updated_at,
                    field_name=field_name,
                    old_value=old_value,
                    new_value=value,
                    updated_by=updated_by,
                    comment=comment,
                ),
            )
            # Collect feedback for accuracy tracking
            doc_type = next((f.value for f in record.fields if f.field_name == "document_type"), "")
            self.feedback_collector.collect_correction(
                document_id=document_id,
                document_type=doc_type,
                field_name=field_name,
                ai_value=old_value,
                corrected_value=value,
                extraction_source=old_source,
            )
            self.storage.append_log(document_id, "info", f"Field updated: {field_name}")
            break

        if not matched:
            self.storage.append_log(document_id, "warning", f"Field not found: {field_name}")
            return record

        record.reviewed_at = None
        record.status = DocumentStatus.NEEDS_REVIEW
        self._sync_items_from_fields(record)
        self.storage.save_final_payload(
            document_id,
            {
                "document_id": document_id,
                "status": record.status.value,
                "fields": [field.model_dump(mode="json") for field in record.fields],
                "items": [item.model_dump(mode="json") for item in record.items],
            },
        )
        self.storage.save_record(record)
        return record

    def finalize_review(self, document_id: str) -> tuple[DocumentRecord, list[str]]:
        record = self.storage.load_record(document_id)
        missing_required = [field.label for field in record.fields if field.required and not field.value]
        if missing_required:
            record.status = DocumentStatus.NEEDS_REVIEW
            self.storage.append_log(document_id, "warning", f"Review blocked; missing required fields: {', '.join(missing_required)}")
            self.storage.save_record(record)
            return record, missing_required

        record.status = DocumentStatus.REVIEWED
        record.reviewed_at = datetime.now(UTC)
        self.storage.append_log(document_id, "info", "Review finalized")
        self.storage.save_final_payload(
            document_id,
            {
                "document_id": document_id,
                "status": record.status.value,
                "fields": [field.model_dump(mode="json") for field in record.fields],
                "items": [item.model_dump(mode="json") for item in record.items],
            },
        )
        self.storage.save_record(record)
        return record, []

    def export(self, document_id: str) -> DocumentRecord:
        record = self.storage.load_record(document_id)
        if record.status not in (DocumentStatus.REVIEWED, DocumentStatus.EXPORTED):
            raise ValueError("검수 완료 후에만 엑셀을 생성할 수 있습니다.")
        output_path = self.storage.export_path(document_id)
        edit_history = self.storage.load_audit(document_id)
        processing_logs = self.storage.load_logs(document_id)
        record.export_file_name = self.exporter.export(
            record,
            output_path,
            edit_history=edit_history,
            processing_logs=processing_logs,
        )
        record.exported_at = datetime.now(UTC)
        record.status = DocumentStatus.EXPORTED
        self.storage.append_log(document_id, "info", "Excel export completed")
        self.storage.save_record(record)
        return record

    def _ocr_pdf(self, document_id: str, source_path: Path, warnings: list[str]) -> str | None:
        """Try Vision API first for PDF OCR, fall back to Tesseract."""
        if self.vision_ocr_service.available:
            try:
                text = self.vision_ocr_service.extract_from_pdf(source_path)
                self.storage.append_log(document_id, "info", "Vision API OCR completed for PDF")
                return text
            except VisionOcrUnavailableError as error:
                self.storage.append_log(document_id, "warning", f"Vision OCR failed, trying Tesseract: {error}")

        try:
            text = self.ocr_service.extract(source_path)
            self.storage.append_log(document_id, "info", "Tesseract OCR completed for PDF")
            return text
        except OcrUnavailableError as error:
            warnings.append(str(error))
            self.storage.append_log(document_id, "error", str(error))
            return None

    def _ocr_image(self, document_id: str, source_path: Path, warnings: list[str]) -> str | None:
        """Try Vision API first for image OCR, fall back to Tesseract."""
        if self.vision_ocr_service.available:
            try:
                text = self.vision_ocr_service.extract_from_image(source_path)
                self.storage.append_log(document_id, "info", "Vision API OCR completed for image")
                return text
            except VisionOcrUnavailableError as error:
                self.storage.append_log(document_id, "warning", f"Vision OCR failed, trying Tesseract: {error}")

        try:
            text = self.ocr_service.extract_image(source_path)
            self.storage.append_log(document_id, "info", "Tesseract OCR completed for image")
            return text
        except OcrUnavailableError as error:
            warnings.append(str(error))
            self.storage.append_log(document_id, "error", str(error))
            return None

    def _sync_items_from_fields(self, record: DocumentRecord) -> None:
        fields_by_name = record.fields_by_name
        if not record.items:
            if any(fields_by_name.get(name) and fields_by_name[name].value for name in ["item_name", "supply_amount", "tax_amount"]):
                record.items = [self._build_default_item(fields_by_name)]
            return

        primary_item = record.items[0]
        if fields_by_name.get("item_name") and fields_by_name["item_name"].value:
            primary_item.item_name = fields_by_name["item_name"].value
        if fields_by_name.get("supply_amount") and fields_by_name["supply_amount"].value:
            primary_item.supply_amount = fields_by_name["supply_amount"].value
        if fields_by_name.get("tax_amount") and fields_by_name["tax_amount"].value:
            primary_item.tax_amount = fields_by_name["tax_amount"].value

    def _build_default_item(self, fields_by_name: dict[str, DocumentField]) -> InvoiceLineItem:
        item_f = fields_by_name.get("item_name")
        supply_f = fields_by_name.get("supply_amount")
        tax_f = fields_by_name.get("tax_amount")
        return InvoiceLineItem(
            item_name=item_f.value if item_f else "",
            supply_amount=supply_f.value if supply_f else "",
            tax_amount=tax_f.value if tax_f else "",
        )
