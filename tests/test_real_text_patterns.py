from app.services.extractor import InvoiceFieldExtractor


extractor = InvoiceFieldExtractor()


def as_map(text: str) -> dict[str, str]:
    outcome = extractor.extract(text)
    return {field.field_name: field.value for field in outcome.fields}


def test_extracts_contract_parties_and_amounts() -> None:
    text = """
개발 용역 계약서
2. 계약금액 이천사백팔십만원정 ($24,800,000 - VAT 별도)
2025년 05월 22일
계약자 (“갑”)
상 호: ㈜ 현보
계약자(“을”)
상 호: ㈜오아시스템
"""
    fields = as_map(text)
    assert fields["document_type"] == "외부용역계약서"
    assert fields["issue_date"] == "2025-05-22"
    assert "현보" in fields["supplier_name"]
    assert "오아시스템" in fields["buyer_name"]
    assert fields["supply_amount"] == "24,800,000"


def test_extracts_estimate_fields() -> None:
    text = """
견 적 서
견적번호 AUA2505-116
견적날짜 05월 28일
수 신 처 ㈜현보
㈜오아시스템
제목 : MCU 다운로드 Aging 테스트 툴 개발 용역
품명 MCU 다운로드 Aging 테스트 툴 개발 용역 규격 MCU 다운로드 Aging 테스트 툴 개발 (GW7120 on WriteNOW!)
합계 24,800,000
VAT 별도
"""
    fields = as_map(text)
    assert fields["document_type"] == "개발용역견적서"
    assert "오아시스템" in fields["supplier_name"]
    assert "현보" in fields["buyer_name"]
    assert fields["item_name"] == "MCU 다운로드 Aging 테스트 툴 개발 용역"
    assert fields["supply_amount"] == "24,800,000"


def test_extracts_real_tax_invoice_layout() -> None:
    text = """
전자세금계산서 승인번호 20250530-10250530-53000457
공급자 등록번호 142-81-83231
공급받는자 등록번호 117-81-03361
상호(법인명) 주식회사 오아시스템 성명 박원국
상호(법인명) (주) 현보 성명 곽태승
작성일자 공급가액 세액 수정사유
2025/05/30 24,800,000 2,480,000
05 30 MCU 다운로드 테스트 툴 개발 용역 Set 1 24,800,000 24,800,000 2,480,000
합계금액 이 금액을 (청구) 함 27,280,000
"""
    fields = as_map(text)
    assert fields["document_type"] == "전자세금계산서"
    assert fields["issue_date"] == "2025-05-30"
    assert "오아시스템" in fields["supplier_name"]
    assert "현보" in fields["buyer_name"]
    assert fields["supplier_biz_no"] == "142-81-83231"
    assert fields["buyer_biz_no"] == "117-81-03361"
    assert fields["supply_amount"] == "24,800,000"
    assert fields["tax_amount"] == "2,480,000"
    assert fields["total_amount"] == "27,280,000"


def test_extracts_statement_amount_triplet_and_parties() -> None:
    text = """
2025-05-28
거  래  명  세  서
공급받는자
공급자 (주)현보
주식회사 오아시스템
등록번호 142-81-83231
품목 WN04A
공급가액 12,500,000
세액 1,250,000
합계금액(VAT포함) 13,750,000
"""
    fields = as_map(text)
    assert fields["document_type"] == "거래명세서"
    assert fields["issue_date"] == "2025-05-28"
    assert "오아시스템" in fields["supplier_name"]
    assert "현보" in fields["buyer_name"]
    assert fields["item_name"] == "WN04A"
    assert fields["supply_amount"] == "12,500,000"
    assert fields["tax_amount"] == "1,250,000"
    assert fields["total_amount"] == "13,750,000"


def test_extracts_general_quote_fields() -> None:
    text = """
윤헌플러스㈜
수신처
현보(주) 귀하
견적날짜 : 2026년 02월 25일
NOTICE :
EOL Tester Program Up_Grade
見 積 書
(QUOTATION)
11,000,000
1,100,000
12,100,000 VAT별도
"""
    fields = as_map(text)
    assert fields["document_type"] == "일반견적서"
    assert fields["issue_date"] == "2026-02-25"
    assert "윤헌플러스" in fields["supplier_name"]
    assert "현보" in fields["buyer_name"]
    assert fields["supply_amount"] == "11,000,000"
    assert fields["tax_amount"] == "1,100,000"
    assert fields["total_amount"] == "12,100,000"
    assert fields["item_name"] == "EOL Tester Program Up_Grade"
