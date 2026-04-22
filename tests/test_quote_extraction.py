from __future__ import annotations

from app.services.extractor import InvoiceFieldExtractor


AIMOTION_QUOTE_TEXT = """견적서
견적번호: 240529-114100
견적일자: 2024년 5월 29일
㈜ 아이모션테크
서울 금천구 디지털로9길 65
수 신: 주식회사 현보
참 조: 김 성 수 연구원님
전 화: 02-6269-1008
담 당 자: 박용연 팀장
금 액: 250,000원
(부가세 별도)
품 명 수량 단위 단가 금 액 소계
CL4-S-MOT 1 250,000 250,000 드라이버+ 컨트롤러
합 계 금 액 1 250,000
*견적조건
1. 견적유효기간 : 견적일로부터 15일간
2. 인 도 장 소 : 귀사 지정장소 상차도
3. 결 제 조 건 : 정기 결제
4. 납 기 : -
5. 특 이 사 항 :"""


class TestAimotionQuote:
    """견적서에서 띄어쓰기 라벨("품 명", "결 제 조 건") 및 테이블 헤더 패턴 지원."""

    def test_classifies_as_quote(self):
        r = InvoiceFieldExtractor().extract(AIMOTION_QUOTE_TEXT)
        doc_type = next((f.value for f in r.fields if f.field_name == "document_type"), "")
        assert "견적" in doc_type

    def test_extracts_payment_terms_as_remark(self):
        r = InvoiceFieldExtractor().extract(AIMOTION_QUOTE_TEXT)
        remark = next((f.value for f in r.fields if f.field_name == "remark"), "")
        assert "정기" in remark or "결제" in remark, f"remark not captured: {remark!r}"

    def test_extracts_item_name_from_table_row(self):
        r = InvoiceFieldExtractor().extract(AIMOTION_QUOTE_TEXT)
        item_name = next((f.value for f in r.fields if f.field_name == "item_name"), "")
        assert item_name == "CL4-S-MOT", f"Expected CL4-S-MOT, got {item_name!r}"

    def test_supplier_recognized(self):
        r = InvoiceFieldExtractor().extract(AIMOTION_QUOTE_TEXT)
        supplier = next((f.value for f in r.fields if f.field_name == "supplier_name"), "")
        assert "아이모션" in supplier

    def test_buyer_recognized(self):
        r = InvoiceFieldExtractor().extract(AIMOTION_QUOTE_TEXT)
        buyer = next((f.value for f in r.fields if f.field_name == "buyer_name"), "")
        assert "현보" in buyer


class TestLabelNormalization:
    """띄어쓰기 들어간 라벨도 정상 매칭."""

    def test_remark_with_spaced_label(self):
        text = "결 제 조 건 : 선금 30% 중도금 40% 잔금 30%"
        r = InvoiceFieldExtractor().extract(text)
        remark = next((f.value for f in r.fields if f.field_name == "remark"), "")
        assert "선금" in remark

    def test_item_with_spaced_header(self):
        text = "견 적 항 목 수량 단가 금액\n웹 시스템 개발 1 1,000,000 1,000,000"
        r = InvoiceFieldExtractor().extract(text)
        item = next((f.value for f in r.fields if f.field_name == "item_name"), "")
        assert "웹" in item or "시스템" in item
