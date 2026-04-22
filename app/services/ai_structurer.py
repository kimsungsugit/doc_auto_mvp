from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from app.confidence_thresholds import FIELD_CONFIDENCE_OK
from app.models import DocumentField, InvoiceLineItem, ValidationStatus
from app.schema import COMMON_FIELDS, TYPE_SPECIFIC_FIELDS, build_empty_fields
from app.services.ai_cache import get_default_cache

if TYPE_CHECKING:
    from openai import OpenAI

logger = logging.getLogger(__name__)

try:
    from openai import APIConnectionError, APIError, RateLimitError
    _OPENAI_API_ERRORS: tuple[type[Exception], ...] = (APIError, APIConnectionError, RateLimitError)
except ImportError:
    _OPENAI_API_ERRORS = ()

_OPENAI_CALL_ERRORS = (*_OPENAI_API_ERRORS, json.JSONDecodeError, RuntimeError)
_OPENAI_INIT_ERRORS = (ImportError, *_OPENAI_API_ERRORS)


# 시스템이 직접 채우는 메타필드. AI 추출 대상에서 제외해야 AI가 혼동해 엉뚱한 값 안 채움.
_SYSTEM_FIELDS: frozenset[str] = frozenset({"document_type", "approval_status", "confidence_score"})

# 유형별 전용 필드에 붙일 설명(프롬프트에서 AI가 어떤 유형에 쓰는지 인지하도록).
# 공통 필드는 schema.py 라벨을 그대로 사용하되, 유형 전용은 여기서 추가 hint 제공.
_FIELD_HINTS: dict[str, str] = {
    "issue_date": "작성일자/발행일/계약일/견적일/거래일자 (YYYY-MM-DD)",
    "supplier_name": "공급자명 / 발행자 / 수행사 / 가맹점명(영수증)",
    "supplier_biz_no": "공급자 사업자등록번호 (XXX-XX-XXXXX)",
    "buyer_name": "공급받는자명 / 수신처 / 발주사 / 고객명(영수증, 보통 없음)",
    "buyer_biz_no": "공급받는자 사업자등록번호 (XXX-XX-XXXXX)",
    "supply_amount": "공급가액 / 계약금액 / 견적금액",
    "tax_amount": "세액 / 부가세 / VAT",
    "total_amount": "합계금액 / 총액",
    "item_name": "대표 품목명 / 계약건명 / 견적 항목",
    "remark": "비고 / 참고사항",
    "approval_no": "승인번호 (영수증 전용, 8~12자리 숫자)",
    "transaction_time": "거래일시 (영수증 전용, YYYY-MM-DD HH:MM)",
    "card_number_masked": "마스킹된 카드번호 (영수증 전용, 예: 1234-56**-****-7890)",
    "service_charge": "봉사료 (영수증 전용, 없으면 빈 문자열)",
}


def _extractable_fields() -> list[tuple[str, str]]:
    """AI가 추출해야 할 (field_name, label) 리스트. 시스템 필드 제외, 공통 ∪ 모든 유형 전용.

    schema.COMMON_FIELDS/TYPE_SPECIFIC_FIELDS가 source of truth — 새 문서 유형을 schema.py에
    추가하면 여기서 자동으로 union에 포함돼 프롬프트와 JSON Schema enum이 함께 갱신됨.
    """
    seen: set[str] = set()
    fields: list[tuple[str, str]] = []
    for field_name, label, _required in COMMON_FIELDS:
        if field_name in _SYSTEM_FIELDS or field_name in seen:
            continue
        seen.add(field_name)
        fields.append((field_name, label))
    for type_fields in TYPE_SPECIFIC_FIELDS.values():
        for field_name, label, _required in type_fields:
            if field_name in _SYSTEM_FIELDS or field_name in seen:
                continue
            seen.add(field_name)
            fields.append((field_name, label))
    return fields


def _extractable_field_names() -> list[str]:
    return [name for name, _label in _extractable_fields()]


def _build_field_list_section() -> str:
    """프롬프트의 'Fields to extract' 블록을 동적 생성. 각 줄: '- name: label — hint'."""
    lines = []
    for name, label in _extractable_fields():
        hint = _FIELD_HINTS.get(name, "")
        if hint:
            lines.append(f"- {name}: {label} — {hint}")
        else:
            lines.append(f"- {name}: {label}")
    return "\n".join(lines)


DOCUMENT_TYPE_PROMPT = """\
You are a Korean business document classifier. Given extracted (possibly OCR'd) text,
return the document type plus confidence. Spaces from OCR may appear inside Korean titles
(e.g. "세 금 계 산 서") — treat them as the same keyword.

Types:
- 전자세금계산서: 전자세금계산서 / 세금계산서 / 수정세금계산서 / Tax Invoice. Has both 공급자 and
  공급받는자 with 사업자등록번호. No 승인번호/카드번호.
- 거래명세서: 거래명세서 / 거래명세표 / 납품명세서 / 납품서 / 물품수령증. Line items with
  quantity/price, usually no VAT-invoice header.
- 외부용역계약서: 외부용역계약서 / 용역계약서 / 개발용역계약서. Has 계약기간, 계약금액,
  갑/을 당사자. NOT a quotation (no 견적번호).
- 개발용역견적서: Quotation with 개발용역 scope. Often has 견적번호 like AUA####-# plus
  개발/SI/용역 context.
- 일반견적서: Generic quotation — QUOTATION / 見積書 / 견적서 without 개발용역 indicators.
- 영수증: 영수증 / 카드매출전표 / 신용카드매출전표 / 현금영수증 / 간이영수증 / POS receipt.
  Signals: 승인번호, masked card number (xxxx-xx**-****), 가맹점, 거래일시.

Tie-breakers (apply IN ORDER):
1. 카드매출전표/현금영수증/승인번호 present AND no 세금계산서 header → 영수증.
2. 세금계산서 keyword present AND 공급자 + 공급받는자 both labeled → 전자세금계산서
   (even if 영수증 appears as a minor word).
3. 거래명세서/거래명세표 header dominates → 거래명세서 (even if 세금계산서 mentioned in footer).
4. 계약서 keyword + 계약기간/계약금액 → 외부용역계약서 (견적서 keyword without 계약기간 → 견적서).
5. 견적서 + 개발용역/SI/AUA견적번호 → 개발용역견적서. Plain 견적서/QUOTATION/見積書 → 일반견적서.

Return empty string with confidence 0.0 if none of the signals apply. Do NOT guess."""

_FIELD_EXTRACTION_RULES = """\
You are an expert Korean business document field extractor.
Extract the following fields from the given document text.

Rules:
- Dates must be in YYYY-MM-DD format
- Business registration numbers must be in XXX-XX-XXXXX format (10 digits with dashes)
- Amounts must include commas (e.g., "1,000,000"), no currency symbols
- Company names should include legal entity markers like (주), ㈜, 주식회사
- Return empty string "" for fields not found in the document
- For each field, provide a confidence score (0.0 to 1.0)
- Document type-specific fields (영수증 전용 등) must still be returned even when
  the document isn't that type — use empty string for value in that case.

Document type: {document_type}

Fields to extract:
{fields_list}"""


def _build_field_extraction_prompt(document_type: str) -> str:
    """현재 스키마 기준으로 필드 목록이 반영된 프롬프트 반환."""
    return _FIELD_EXTRACTION_RULES.format(
        document_type=document_type,
        fields_list=_build_field_list_section(),
    )


# 호환 목적: 기존 테스트/외부 import가 FIELD_EXTRACTION_PROMPT 이름을 참조할 수 있음.
# rules + 현재 스키마 기반 필드 리스트로 한 번 생성해 모듈 상수로 노출.
FIELD_EXTRACTION_PROMPT = _build_field_extraction_prompt("(use the document_type you classified above)")

LINE_ITEMS_PROMPT = """\
You are an expert Korean business document table extractor.
Extract ALL line items (품목/항목) from the document as a structured list.

Rules:
- Extract every row from the item table, not just the first one
- Amounts must include commas (e.g., "1,000,000")
- Return empty string "" for cells not found
- If there is only one item, still return it as a single-element array

Document type: {document_type}

For each item, extract:
- item_name: 품목명/항목명
- item_spec: 규격/사양
- quantity: 수량
- unit_price: 단가
- supply_amount: 공급가액
- tax_amount: 세액"""


class OpenAIStructurer:
    def __init__(self) -> None:
        self.enabled = os.getenv("OPENAI_STRUCTURING_ENABLED", "").lower() in {"1", "true", "yes"}
        self.model = os.getenv("OPENAI_STRUCTURING_MODEL", "gpt-4o-mini")
        self._client: OpenAI | None = None
        self._client_resolved = False

    def _get_client(self) -> OpenAI | None:
        if self._client_resolved:
            return self._client
        self._client_resolved = True
        if not self.enabled:
            return None
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=api_key,
                timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30")),
                max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "3")),
            )
        except _OPENAI_INIT_ERRORS as exc:
            # error 수준 — 활성화 의도(env 설정)가 있는데 실제론 못 쓰는 상태라
            # 규칙 기반으로 silent fallback되는 걸 운영자가 놓치지 않게.
            logger.error("OpenAI client 초기화 실패 — AI 경로 비활성 상태로 fallback: %s", exc)
            self._client = None
        return self._client

    # ── Combined Classification + Extraction (single API call) ────────

    def classify_and_extract(self, text: str) -> tuple[str, float, list[DocumentField]]:
        """Classify and extract fields in a single API call. Returns (document_type, confidence, fields)."""
        client = self._get_client()
        if not client:
            return "", 0.0, []

        schema = self._combined_schema()
        combined_prompt = (
            DOCUMENT_TYPE_PROMPT
            + "\n\n"
            + "After classifying the document type, extract fields using the rules below.\n\n"
            + _build_field_extraction_prompt("(use the document_type you classified above)")
        )

        cache = get_default_cache()
        api_text = text[:6000]
        # 전체 원문으로 키 생성 → 상단 boilerplate가 같은 다른 문서의 충돌 방지
        payload = cache.get("classify_and_extract", combined_prompt, self.model, text)

        if payload is None:
            try:
                response = client.responses.create(
                    model=self.model,
                    input=[
                        {"role": "system", "content": [{"type": "input_text", "text": combined_prompt}]},
                        {"role": "user", "content": [{"type": "input_text", "text": f"Document text:\n{api_text}"}]},
                    ],
                    text={"format": {"type": "json_schema", "name": "classify_and_extract", "schema": schema, "strict": True}},
                )
            except _OPENAI_CALL_ERRORS as exc:
                logger.warning("classify_and_extract API call failed: %s", exc)
                return "", 0.0, []

            payload = self._extract_output_json(response)
            if payload:
                cache.set("classify_and_extract", combined_prompt, self.model, text, payload)

        if not payload:
            return "", 0.0, []

        doc_type = payload.get("document_type", "")
        confidence = float(payload.get("classification_confidence", 0.0))
        fields_data = payload.get("fields", [])

        template_fields = {f.field_name: f for f in build_empty_fields(doc_type)}
        result: list[DocumentField] = []
        for field_data in fields_data:
            field_name = field_data.get("field_name", "")
            value = field_data.get("value", "").strip()
            field_confidence = float(field_data.get("confidence", 0.0))
            if field_name in template_fields:
                field = template_fields[field_name]
                field.value = value
                field.confidence = round(field_confidence, 2)
                field.extraction_source = "ai"
                field.source_snippet = f"AI extracted ({self.model})"
                if value:
                    field.validation_status = ValidationStatus.OK if field_confidence >= FIELD_CONFIDENCE_OK else ValidationStatus.WARNING
                elif field.required:
                    field.validation_status = ValidationStatus.MISSING
                result.append(field)

        returned_names = {f.field_name for f in result}
        for field_name, field in template_fields.items():
            if field_name not in returned_names:
                result.append(field)

        return doc_type, confidence, result

    # ── Document Classification ──────────────────────────────────────

    def classify_document(self, text: str) -> tuple[str, float]:
        """Classify document type using AI. Returns (document_type, confidence)."""
        client = self._get_client()
        if not client:
            return "", 0.0

        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "document_type": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["document_type", "confidence"],
        }

        try:
            response = client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": [{"type": "input_text", "text": DOCUMENT_TYPE_PROMPT}]},
                    {"role": "user", "content": [{"type": "input_text", "text": f"Document text:\n{text[:4000]}"}]},
                ],
                text={"format": {"type": "json_schema", "name": "document_classification", "schema": schema, "strict": True}},
            )
        except _OPENAI_CALL_ERRORS as exc:
            logger.warning("classify_document API call failed: %s", exc)
            return "", 0.0

        payload = self._extract_output_json(response)
        doc_type = payload.get("document_type", "")
        confidence = float(payload.get("confidence", 0.0))
        return doc_type, confidence

    # ── Field Extraction ─────────────────────────────────────────────

    def extract_fields(self, text: str, document_type: str) -> list[DocumentField]:
        """Extract all fields from document text using AI. Returns list of DocumentField."""
        client = self._get_client()
        if not client:
            return []

        schema = self._field_extraction_schema()
        prompt = _build_field_extraction_prompt(document_type)

        try:
            response = client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": [{"type": "input_text", "text": prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": f"Document text:\n{text[:6000]}"}]},
                ],
                text={"format": {"type": "json_schema", "name": "field_extraction", "schema": schema, "strict": True}},
            )
        except _OPENAI_CALL_ERRORS as exc:
            logger.warning("extract_fields API call failed: %s", exc)
            return []

        payload = self._extract_output_json(response)
        if not payload or "fields" not in payload:
            return []

        template_fields = {f.field_name: f for f in build_empty_fields(document_type)}
        result: list[DocumentField] = []

        for field_data in payload["fields"]:
            field_name = field_data.get("field_name", "")
            value = field_data.get("value", "").strip()
            confidence = float(field_data.get("confidence", 0.0))

            if field_name in template_fields:
                field = template_fields[field_name]
                field.value = value
                field.confidence = round(confidence, 2)
                field.extraction_source = "ai"
                field.source_snippet = f"AI extracted ({self.model})"
                if value:
                    field.validation_status = ValidationStatus.OK if confidence >= FIELD_CONFIDENCE_OK else ValidationStatus.WARNING
                elif field.required:
                    field.validation_status = ValidationStatus.MISSING
                result.append(field)

        # Add any template fields not returned by AI (keep default extraction_source)
        returned_names = {f.field_name for f in result}
        for field_name, field in template_fields.items():
            if field_name not in returned_names:
                result.append(field)

        return result

    # ── Line Items Extraction ────────────────────────────────────────

    def extract_line_items(self, text: str, document_type: str) -> list[InvoiceLineItem]:
        """Extract line items (table rows) from document text using AI."""
        client = self._get_client()
        if not client:
            return []

        schema = self._line_items_schema()
        prompt = LINE_ITEMS_PROMPT.format(document_type=document_type)

        try:
            response = client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": [{"type": "input_text", "text": prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": f"Document text:\n{text[:6000]}"}]},
                ],
                text={"format": {"type": "json_schema", "name": "line_items_extraction", "schema": schema, "strict": True}},
            )
        except _OPENAI_CALL_ERRORS as exc:
            logger.warning("extract_line_items API call failed: %s", exc)
            return []

        payload = self._extract_output_json(response)
        if not payload or "items" not in payload:
            return []

        items: list[InvoiceLineItem] = []
        for index, item_data in enumerate(payload["items"], start=1):
            items.append(InvoiceLineItem(
                line_number=index,
                item_name=item_data.get("item_name", ""),
                item_spec=item_data.get("item_spec", ""),
                quantity=item_data.get("quantity", ""),
                unit_price=item_data.get("unit_price", ""),
                supply_amount=item_data.get("supply_amount", ""),
                tax_amount=item_data.get("tax_amount", ""),
            ))
        return items

    # ── Legacy Refinement (backward compatible) ──────────────────────

    def maybe_refine(self, text: str, fields: list[DocumentField]) -> list[DocumentField]:
        """Refine rule-based extraction results with AI. Legacy method for backward compatibility."""
        client = self._get_client()
        if not client:
            return fields

        current_fields = {field.field_name: field.value for field in fields}
        schema = self._legacy_response_schema()

        try:
            response = client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": (
                            "You extract Korean business document fields. "
                            "Return normalized field values with confidence scores. "
                            "Dates: YYYY-MM-DD, Biz numbers: XXX-XX-XXXXX, Amounts: with commas. "
                            "Keep empty strings when not found."
                        )}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": f"Source text:\n{text[:6000]}\n\nCurrent fields:\n{json.dumps(current_fields, ensure_ascii=False)}"}],
                    },
                ],
                text={"format": {"type": "json_schema", "name": "vat_invoice_fields", "schema": schema, "strict": True}},
            )
        except _OPENAI_CALL_ERRORS as exc:
            logger.warning("maybe_refine API call failed: %s", exc)
            return fields

        payload = self._extract_output_json(response)
        if not payload:
            return fields

        for field in fields:
            field_data = payload.get(field.field_name)
            if isinstance(field_data, dict):
                value = field_data.get("value", "").strip()
                confidence = float(field_data.get("confidence", 0.0))
                if value:
                    field.value = value
                    field.confidence = max(field.confidence, round(confidence, 2))
                    field.extraction_source = "ai"
            elif isinstance(field_data, str) and field_data.strip():
                field.value = field_data.strip()
                field.confidence = max(field.confidence, 0.91)
                field.extraction_source = "ai"
        return fields

    # ── JSON Helpers ─────────────────────────────────────────────────

    def _extract_output_json(self, response: Any) -> dict[str, Any]:
        if hasattr(response, "output_text") and response.output_text:
            try:
                return json.loads(response.output_text)
            except json.JSONDecodeError:
                return {}
        return {}

    # ── Schema Definitions ───────────────────────────────────────────

    def _combined_schema(self) -> dict[str, Any]:
        # field_name을 스키마 전체 추출 가능 필드로 제한 → AI가 정의되지 않은 이름을 반환할 수 없음
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "document_type": {"type": "string"},
                "classification_confidence": {"type": "number"},
                "fields": {"type": "array", "items": self._field_item_schema()},
            },
            "required": ["document_type", "classification_confidence", "fields"],
        }

    def _field_extraction_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "fields": {"type": "array", "items": self._field_item_schema()},
            },
            "required": ["fields"],
        }

    @staticmethod
    def _field_item_schema() -> dict[str, Any]:
        """Fields 배열의 각 원소 스키마. field_name은 스키마 기반 enum으로 제한."""
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "field_name": {"type": "string", "enum": _extractable_field_names()},
                "value": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["field_name", "value", "confidence"],
        }

    def _line_items_schema(self) -> dict[str, Any]:
        item_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "item_name": {"type": "string"},
                "item_spec": {"type": "string"},
                "quantity": {"type": "string"},
                "unit_price": {"type": "string"},
                "supply_amount": {"type": "string"},
                "tax_amount": {"type": "string"},
            },
            "required": ["item_name", "item_spec", "quantity", "unit_price", "supply_amount", "tax_amount"],
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "items": {"type": "array", "items": item_schema},
            },
            "required": ["items"],
        }

    def _legacy_response_schema(self) -> dict[str, Any]:
        field_with_confidence = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "value": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["value", "confidence"],
        }
        field_names = [
            "document_type", "issue_date", "supplier_name", "supplier_biz_no",
            "buyer_name", "buyer_biz_no", "supply_amount", "tax_amount",
            "total_amount", "item_name", "remark",
        ]
        properties = {name: field_with_confidence for name in field_names}
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": field_names,
        }
