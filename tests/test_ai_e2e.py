"""AI E2E tests — only run when OPENAI_API_KEY is set.

These tests call the real OpenAI API to verify classify_and_extract
and extract_line_items work correctly with sample document texts.

Run:
    OPENAI_STRUCTURING_ENABLED=true OPENAI_API_KEY=sk-... python -m pytest tests/test_ai_e2e.py -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_CAPTURED_API_KEY = os.getenv("OPENAI_API_KEY", "")
_CAPTURED_STRUCTURING = os.getenv("OPENAI_STRUCTURING_ENABLED", "")

try:
    import openai  # noqa: F401
    _OPENAI_IMPORTABLE = True
except ImportError:
    _OPENAI_IMPORTABLE = False

AI_AVAILABLE = (
    _CAPTURED_STRUCTURING.lower() in {"1", "true", "yes"}
    and bool(_CAPTURED_API_KEY)
    and _OPENAI_IMPORTABLE
)

pytestmark = pytest.mark.skipif(
    not AI_AVAILABLE,
    reason="OPENAI_API_KEY/STRUCTURING not set, or openai package not installed in this Python env",
)

SAMPLES_PATH = Path(__file__).resolve().parent.parent / "samples" / "sample_cases.json"


@pytest.fixture(autouse=True)
def _restore_ai_env(_isolate_env, monkeypatch):
    """conftest의 _isolate_env가 지운 OPENAI_* env를 복원.
    AI E2E는 실제 API 호출이 목적이라 격리 규칙에서 의도적으로 예외."""
    if _CAPTURED_API_KEY:
        monkeypatch.setenv("OPENAI_API_KEY", _CAPTURED_API_KEY)
    if _CAPTURED_STRUCTURING:
        monkeypatch.setenv("OPENAI_STRUCTURING_ENABLED", _CAPTURED_STRUCTURING)


def load_sample_cases() -> list[dict]:
    if not SAMPLES_PATH.exists():
        return []
    return json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def structurer():
    from app.services.ai_structurer import OpenAIStructurer
    return OpenAIStructurer()


@pytest.fixture(scope="module")
def sample_cases() -> list[dict]:
    return load_sample_cases()


# ── classify_and_extract tests ──────────────────────────────────────


def test_classify_and_extract_returns_fields(structurer, sample_cases) -> None:
    """Basic smoke test: classify_and_extract returns non-empty results."""
    if not sample_cases:
        pytest.skip("No sample cases found")

    case = sample_cases[0]
    text = "\n".join(case["lines"])
    doc_type, confidence, fields = structurer.classify_and_extract(text)

    assert doc_type, "Document type should not be empty"
    assert confidence > 0.0, "Confidence should be positive"
    assert len(fields) > 0, "Should return at least some fields"


def test_classify_and_extract_matches_expected_fields(structurer, sample_cases) -> None:
    """Test that AI extraction matches expected values for the first 5 sample cases."""
    if not sample_cases:
        pytest.skip("No sample cases found")

    total_fields = 0
    matched_fields = 0

    for case in sample_cases[:5]:
        text = "\n".join(case["lines"])
        _doc_type, _confidence, fields = structurer.classify_and_extract(text)
        field_map = {f.field_name: f.value for f in fields}

        for key, expected_value in case["expected"].items():
            total_fields += 1
            actual = field_map.get(key, "")
            if actual == expected_value:
                matched_fields += 1

    ratio = matched_fields / total_fields if total_fields else 0
    assert ratio >= 0.7, f"AI accuracy too low: {matched_fields}/{total_fields} ({ratio:.0%}), expected >= 70%"


def test_classify_detects_document_type(structurer) -> None:
    """Test that AI correctly classifies common document types."""
    tax_invoice_text = "전자세금계산서\n작성일자 2026-04-06\n공급자 123-45-67890\n공급가액 100,000\n세액 10,000"
    doc_type, confidence, _fields = structurer.classify_and_extract(tax_invoice_text)
    assert "세금계산서" in doc_type, f"Expected 세금계산서, got {doc_type}"
    assert confidence >= 0.7


def test_classify_detects_estimate(structurer) -> None:
    """Test that AI correctly classifies a quotation."""
    estimate_text = "견 적 서\n견적번호 AUA2505-001\n견적날짜 05월 01일\n수신처 ㈜테스트\n합계 10,000,000"
    doc_type, confidence, _fields = structurer.classify_and_extract(estimate_text)
    assert "견적" in doc_type, f"Expected 견적서 type, got {doc_type}"
    assert confidence >= 0.7


# ── extract_line_items tests ────────────────────────────────────────


def test_extract_line_items_returns_items(structurer) -> None:
    """Test that extract_line_items returns at least one item."""
    text = "전자세금계산서\n품목 소프트웨어 개발\n수량 1\n단가 5,000,000\n공급가액 5,000,000\n세액 500,000"
    items = structurer.extract_line_items(text, "전자세금계산서")
    assert len(items) >= 1, "Should extract at least one line item"
    assert items[0].item_name, "Item name should not be empty"


def test_extract_line_items_multiple(structurer) -> None:
    """Test that extract_line_items can extract multiple items."""
    text = (
        "전자세금계산서\n"
        "품목1 서버 개발 수량 1 단가 3,000,000 공급가액 3,000,000\n"
        "품목2 프론트 개발 수량 1 단가 2,000,000 공급가액 2,000,000\n"
        "합계 5,000,000"
    )
    items = structurer.extract_line_items(text, "전자세금계산서")
    assert len(items) >= 2, f"Should extract multiple items, got {len(items)}"
