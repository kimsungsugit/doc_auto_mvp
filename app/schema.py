from __future__ import annotations

import json
import threading
from pathlib import Path

from app.models import DocumentField, ValidationStatus

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "document_types.json"

COMMON_FIELDS = [
    ("document_type", "문서 유형", True),
    ("issue_date", "작성일자", True),
    ("supplier_name", "공급자명", True),
    ("supplier_biz_no", "공급자 사업자번호", True),
    ("buyer_name", "공급받는자명", True),
    ("buyer_biz_no", "공급받는자 사업자번호", False),
    ("supply_amount", "공급가액", True),
    ("tax_amount", "세액", True),
    ("total_amount", "합계금액", True),
    ("item_name", "품목", False),
    ("remark", "비고", False),
    ("approval_status", "검수 상태", True),
    ("confidence_score", "문서 신뢰도", True),
]

# 특정 문서 유형에만 추가되는 필드. 다른 유형의 감사 시트·피드백 통계를 오염시키지 않음.
TYPE_SPECIFIC_FIELDS: dict[str, list[tuple[str, str, bool]]] = {
    "영수증": [
        ("approval_no", "승인번호", False),
        ("transaction_time", "거래일시", False),
        ("card_number_masked", "카드번호", False),
        ("service_charge", "봉사료", False),
    ],
}


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


_CONFIG_CACHE: dict | None = None
_CONFIG_MTIME: float = 0.0
_CONFIG_LOCK = threading.Lock()


def _get_config() -> dict:
    global _CONFIG_CACHE, _CONFIG_MTIME
    with _CONFIG_LOCK:
        if _CONFIG_CACHE is None:
            _CONFIG_CACHE = _load_config()
            if CONFIG_PATH.exists():
                _CONFIG_MTIME = CONFIG_PATH.stat().st_mtime
        elif CONFIG_PATH.exists():
            current_mtime = CONFIG_PATH.stat().st_mtime
            if current_mtime > _CONFIG_MTIME:
                _CONFIG_CACHE = _load_config()
                _CONFIG_MTIME = current_mtime
        return _CONFIG_CACHE


def reload_config() -> None:
    """Force reload config from disk. Useful for testing and development."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    with _CONFIG_LOCK:
        _CONFIG_CACHE = None
        _CONFIG_MTIME = 0.0


def get_supported_document_types() -> list[str]:
    return list(_get_config().keys())


def resolve_schema_name(document_type: str) -> str:
    if document_type in _get_config():
        return document_type
    return "전자세금계산서"


def build_empty_fields(document_type: str = "") -> list[DocumentField]:
    schema_name = resolve_schema_name(document_type)
    config = _get_config()
    overrides = {}
    if schema_name in config:
        raw_overrides = config[schema_name].get("field_overrides", {})
        overrides = {k: tuple(v) for k, v in raw_overrides.items()}

    type_specific = TYPE_SPECIFIC_FIELDS.get(schema_name, [])
    all_fields = list(COMMON_FIELDS) + list(type_specific)

    return [
        DocumentField(
            field_name=field_name,
            label=overrides.get(field_name, (label, required))[0],
            required=overrides.get(field_name, (label, required))[1],
            validation_status=ValidationStatus.MISSING if overrides.get(field_name, (label, required))[1] else ValidationStatus.WARNING,
        )
        for field_name, label, required in all_fields
    ]


def get_field_groups(document_type: str) -> list[tuple[str, list[str]]]:
    schema_name = resolve_schema_name(document_type)
    config = _get_config()
    if schema_name in config:
        groups = config[schema_name].get("field_groups", [])
        return [(title, fields) for title, fields in groups]

    # Fallback to default
    return [
        ("기본 정보", ["document_type", "issue_date", "approval_status", "confidence_score"]),
        ("거래 주체", ["supplier_name", "supplier_biz_no", "buyer_name", "buyer_biz_no"]),
        ("금액 정보", ["supply_amount", "tax_amount", "total_amount"]),
        ("품목 및 비고", ["item_name", "remark"]),
    ]
