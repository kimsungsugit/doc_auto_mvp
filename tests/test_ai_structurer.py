from __future__ import annotations

import os

from app.models import DocumentField, ValidationStatus
from app.services.ai_structurer import OpenAIStructurer


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
