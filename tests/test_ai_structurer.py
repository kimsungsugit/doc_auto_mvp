from __future__ import annotations

import os

from app.models import DocumentField, ValidationStatus
from app.services.ai_structurer import (
    OpenAIStructurer,
    _build_field_list_section,
    _extractable_field_names,
)


def test_ai_structurer_is_noop_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_STRUCTURING_ENABLED", raising=False)
    fields = [
        DocumentField(field_name="supplier_name", label="공급자명", value="기존값", validation_status=ValidationStatus.WARNING),
    ]
    structurer = OpenAIStructurer()
    result = structurer.maybe_refine("sample", fields)
    assert result[0].value == "기존값"


def test_ai_structurer_refines_fields_with_mocked_client(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_STRUCTURING_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class FakeResponse:
        output_text = '{"supplier_name":"보정상호","tax_amount":"20,000"}'

    class FakeResponses:
        def create(self, **_kwargs):
            return FakeResponse()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    import app.services.ai_structurer as module

    monkeypatch.setattr(module, "OpenAI", FakeClient, raising=False)
    monkeypatch.setitem(module.__dict__, "OpenAI", FakeClient)

    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai":
            class FakeOpenAIModule:
                OpenAI = FakeClient
            return FakeOpenAIModule()
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    fields = [
        DocumentField(field_name="supplier_name", label="공급자명", value="기존값", validation_status=ValidationStatus.WARNING),
        DocumentField(field_name="tax_amount", label="세액", value="", validation_status=ValidationStatus.MISSING),
    ]
    result = OpenAIStructurer().maybe_refine("sample text", fields)
    mapped = {field.field_name: field for field in result}
    assert mapped["supplier_name"].value == "보정상호"
    assert mapped["tax_amount"].value == "20,000"
    assert mapped["supplier_name"].confidence >= 0.91


def test_ai_structurer_falls_back_on_client_error(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_STRUCTURING_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class FakeResponses:
        def create(self, **_kwargs):
            raise RuntimeError("boom")

    class FakeClient:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openai":
            class FakeOpenAIModule:
                OpenAI = FakeClient

            return FakeOpenAIModule()
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    fields = [
        DocumentField(field_name="supplier_name", label="공급자명", value="기존값", validation_status=ValidationStatus.WARNING),
    ]
    result = OpenAIStructurer().maybe_refine("sample text", fields)
    assert result[0].value == "기존값"


# ── 동적 프롬프트/스키마 테스트 ──────────────────────────────────────

def test_field_list_section_includes_receipt_specific_fields() -> None:
    """영수증 전용 4필드가 프롬프트 필드 블록에 모두 포함돼야 AI가 추출을 시도함."""
    section = _build_field_list_section()
    for receipt_field in ("approval_no", "transaction_time", "card_number_masked", "service_charge"):
        assert receipt_field in section, f"{receipt_field} not in prompt field list"


def test_field_list_section_excludes_system_fields() -> None:
    """시스템이 채우는 메타필드는 프롬프트에 노출되지 않아야 AI 혼동 방지."""
    section = _build_field_list_section()
    # approval_status/confidence_score는 시스템이 세팅, document_type은 별도 필드로 이미 존재
    assert "\n- document_type:" not in section
    assert "\n- approval_status:" not in section
    assert "\n- confidence_score:" not in section


def test_extractable_field_names_schema_enum_shape() -> None:
    """JSON Schema field_name enum에 들어갈 리스트가 유형 전용 필드까지 포괄하는지."""
    names = _extractable_field_names()
    names_set = set(names)
    # 공통
    assert {"supplier_name", "supplier_biz_no", "supply_amount", "tax_amount", "total_amount"} <= names_set
    # 영수증 전용
    assert {"approval_no", "transaction_time", "card_number_masked", "service_charge"} <= names_set
    # 시스템 필드 제외
    assert "document_type" not in names_set
    assert "approval_status" not in names_set
    assert "confidence_score" not in names_set
    # 중복 없음 (공통+전용 union이 set 크기와 일치)
    assert len(names) == len(names_set)


def test_combined_schema_includes_field_enum() -> None:
    """Responses API strict JSON Schema에 enum 제약이 실제 들어갔는지."""
    schema = OpenAIStructurer()._combined_schema()
    field_name_schema = schema["properties"]["fields"]["items"]["properties"]["field_name"]
    assert "enum" in field_name_schema
    assert "approval_no" in field_name_schema["enum"]
