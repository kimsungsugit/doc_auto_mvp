from __future__ import annotations

from app.services.extractor import InvoiceFieldExtractor


extractor = InvoiceFieldExtractor()


def field_map(text: str) -> dict[str, str]:
    result = extractor.extract(text)
    return {field.field_name: field.value for field in result.fields}


def test_extractor_parses_inline_party_lines() -> None:
    fields = field_map(
        "\n".join(
            [
                "전자세금계산서",
                "작성일자 2026-04-06",
                "공급자 123-45-67890 ABC상사",
                "공급받는자 234-56-78901 테스트고객",
                "공급가액 100,000",
                "세액 10,000",
                "합계 110,000",
                "품목: 유지보수",
            ]
        )
    )
    assert fields["issue_date"] == "2026-04-06"
    assert fields["supplier_biz_no"] == "123-45-67890"
    assert fields["buyer_biz_no"] == "234-56-78901"
    assert fields["supplier_name"] == "ABC상사"
    assert fields["buyer_name"] == "테스트고객"
    assert fields["total_amount"] == "110,000"


def test_extractor_parses_colon_based_layout() -> None:
    fields = field_map(
        "\n".join(
            [
                "전자세금계산서",
                "작성년월일 2026.04.07",
                "공급자: 111-22-33333 미래테스트",
                "공급받는자: 222-33-44444 청강물산",
                "공급가액 250,000원",
                "세액: 25,000원",
                "총액: 275,000원",
                "비고: 4월 정기 청구",
            ]
        )
    )
    assert fields["issue_date"] == "2026-04-07"
    assert fields["supplier_name"] == "미래테스트"
    assert fields["buyer_name"] == "청강물산"
    assert fields["supply_amount"] == "250,000"
    assert fields["tax_amount"] == "25,000"
    assert fields["total_amount"] == "275,000"
    assert fields["remark"] == "4월 정기 청구"


def test_extractor_uses_amount_fallback_when_labels_are_sparse() -> None:
    fields = field_map(
        "\n".join(
            [
                "전자세금계산서",
                "작성일자-2026/04/08",
                "공급자 333-44-55555 오션테크",
                "공급받는자 444-55-66666 리버상사",
                "금액 정보 공급가액 300,000 세액 30,000 합계 330,000",
                "품명 클라우드 서비스",
            ]
        )
    )
    assert fields["supplier_name"] == "오션테크"
    assert fields["buyer_name"] == "리버상사"
    assert fields["supply_amount"] == "300,000"
    assert fields["tax_amount"] == "30,000"
    assert fields["total_amount"] == "330,000"
    assert fields["item_name"] == "클라우드 서비스"


def test_extractor_collects_multiple_items() -> None:
    result = extractor.extract(
        "\n".join(
            [
                "전자세금계산서",
                "작성일자 2026-04-16",
                "공급자 127-77-77777 하늘시스템",
                "공급받는자 238-88-88888 도약물산",
                "품목 보안 점검 정기권",
                "품목 메일 아카이빙 서비스",
                "공급가액 420,000",
                "세액 42,000",
                "합계 462,000",
            ]
        )
    )
    assert len(result.items) == 2
    assert result.items[0].item_name == "보안 점검 정기권"
    assert result.items[1].item_name == "메일 아카이빙 서비스"


def test_extractor_detects_statement_document_type() -> None:
    fields = field_map(
        "\n".join(
            [
                "거래명세서",
                "작성일자 2026-04-19",
                "공급자 131-11-11111 코어시스템",
                "공급받는자 242-22-22222 리드상사",
                "공급가액 330,000",
                "세액 33,000",
                "합계 363,000",
            ]
        )
    )
    assert fields["document_type"] == "거래명세서"
    assert fields["supplier_name"] == "코어시스템"
