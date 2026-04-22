from __future__ import annotations

from app.schema import _get_config

SUMMARY_SHEET_NAME = "문서입력요약"
ITEM_SHEET_NAME = "품목내역"
AUDIT_SHEET_NAME = "검수기록"

SUMMARY_TITLE_CELL = "A1"
SUMMARY_TITLE_RANGE = "A1:F1"

SUMMARY_CELL_MAP = {
    "document_type": "B3",
    "issue_date": "F3",
    "supplier_name": "B6",
    "supplier_biz_no": "F6",
    "buyer_name": "B8",
    "buyer_biz_no": "F8",
    "remark": "B10",
    "supply_amount": "B14",
    "tax_amount": "D14",
    "total_amount": "F14",
    "approval_status": "B17",
    "confidence_score": "F17",
}

DEFAULT_SUMMARY_LAYOUT = {
    SUMMARY_TITLE_CELL: "문서 자동입력 결과",
    "A3": "문서 유형",
    "E3": "작성일자",
    "A6": "공급자명",
    "E6": "공급자 사업자번호",
    "A8": "공급받는자명",
    "E8": "공급받는자 사업자번호",
    "A10": "비고",
    "A14": "공급가액",
    "C14": "세액",
    "E14": "합계금액",
    "A17": "검수상태",
    "E17": "문서 신뢰도",
}

SUMMARY_CONTEXT_START_ROW = 20
SUMMARY_CONTEXT_HEADERS = ("요약 항목", "값", "검수 메모")

ITEM_ROW_START = 3
ITEM_COLUMNS = {
    "item_name": "A",
    "item_spec": "B",
    "quantity": "C",
    "unit_price": "D",
    "supply_amount": "E",
    "tax_amount": "F",
}

DEFAULT_ITEM_HEADERS = {
    "A2": "항목",
    "B2": "규격",
    "C2": "수량",
    "D2": "단가",
    "E2": "공급가액",
    "F2": "세액",
}


def get_summary_layout(document_type: str) -> dict[str, str]:
    config = _get_config()
    if document_type in config:
        return config[document_type].get("summary_layout", DEFAULT_SUMMARY_LAYOUT)
    return DEFAULT_SUMMARY_LAYOUT


def get_summary_context(document_type: str) -> list[tuple[str, str, str]]:
    config = _get_config()
    if document_type in config:
        raw = config[document_type].get("summary_context", [])
        return [tuple(item) for item in raw]
    return [
        ("핵심 체크", "공급가액 + 세액 = 합계금액", "국세청 발행본과 금액 일치 여부 확인"),
        ("검수 우선순위", "사업자번호 / 거래상대 / 승인상태", "누락 시 검수 단계에서 보정"),
    ]


def get_item_headers(document_type: str) -> dict[str, str]:
    config = _get_config()
    if document_type in config:
        return config[document_type].get("item_headers", DEFAULT_ITEM_HEADERS)
    return DEFAULT_ITEM_HEADERS


def get_item_sheet_title(document_type: str) -> str:
    config = _get_config()
    if document_type in config:
        return config[document_type].get("item_sheet_title", "품목내역")
    return "품목내역"
