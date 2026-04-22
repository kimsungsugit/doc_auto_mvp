from __future__ import annotations

from app.services.extractor import InvoiceFieldExtractor
from app.schema import build_empty_fields, get_field_groups


CARD_SLIP = """신용카드매출전표
가맹점명: 스타벅스 강남점
가맹점 사업자번호: 123-45-67890
가맹점 주소: 서울시 강남구 역삼동
승인번호: 12345678
카드번호: 5678-**-****-1234
거래일시: 2024-04-20 14:30:15
공급가액: 9,091
부가세: 909
합계: 10,000
봉사료: 0
"""

CASH_RECEIPT = """현금영수증
가맹점 ABC마트 (987-65-43210)
승인번호 30987654321
거래일시 2024-05-01 18:22
총 결제금액 25,000
"""


class TestReceiptSchema:
    def test_receipt_type_registered(self):
        groups = get_field_groups("영수증")
        titles = [t for t, _ in groups]
        assert any("가맹점" in t for t in titles)
        assert any("결제" in t for t in titles)

    def test_receipt_fields_available(self):
        fields = build_empty_fields("영수증")
        names = {f.field_name for f in fields}
        assert {"approval_no", "transaction_time", "card_number_masked", "service_charge"}.issubset(names)


class TestCardSalesSlip:
    def test_classifies_as_receipt(self):
        r = InvoiceFieldExtractor().extract(CARD_SLIP)
        doc_type = next((f.value for f in r.fields if f.field_name == "document_type"), "")
        assert doc_type == "영수증"

    def test_extracts_approval_no(self):
        r = InvoiceFieldExtractor().extract(CARD_SLIP)
        approval = next((f.value for f in r.fields if f.field_name == "approval_no"), "")
        assert approval == "12345678"

    def test_extracts_transaction_time(self):
        r = InvoiceFieldExtractor().extract(CARD_SLIP)
        ts = next((f.value for f in r.fields if f.field_name == "transaction_time"), "")
        assert ts == "2024-04-20 14:30:15"

    def test_extracts_card_number_masked(self):
        r = InvoiceFieldExtractor().extract(CARD_SLIP)
        card = next((f.value for f in r.fields if f.field_name == "card_number_masked"), "")
        assert card.startswith("5678")
        assert "*" in card
        assert card.endswith("1234")

    def test_extracts_merchant_biz_no(self):
        r = InvoiceFieldExtractor().extract(CARD_SLIP)
        biz = next((f.value for f in r.fields if f.field_name == "supplier_biz_no"), "")
        assert biz == "123-45-67890"


class TestCashReceipt:
    def test_classifies_as_receipt(self):
        r = InvoiceFieldExtractor().extract(CASH_RECEIPT)
        doc_type = next((f.value for f in r.fields if f.field_name == "document_type"), "")
        assert doc_type == "영수증"

    def test_extracts_approval_no(self):
        r = InvoiceFieldExtractor().extract(CASH_RECEIPT)
        approval = next((f.value for f in r.fields if f.field_name == "approval_no"), "")
        assert approval == "30987654321"


class TestNonReceiptDocumentsUnaffected:
    def test_tax_invoice_still_classified(self):
        text = "전자세금계산서\n작성일자 2024-04-01\n공급자 111-11-11111 A상사\n공급받는자 222-22-22222 B상사\n공급가액 100,000 세액 10,000 합계 110,000"
        r = InvoiceFieldExtractor().extract(text)
        doc_type = next((f.value for f in r.fields if f.field_name == "document_type"), "")
        assert doc_type == "전자세금계산서"

    def test_quote_unaffected_by_receipt_keywords(self):
        # 견적서에 "승인번호" 단어가 있어도 영수증이 아님
        text = "견적서\n견적일자 2024-04-10\n견적 항목 웹시스템 개발\n합계 1,000,000\n(비고: 발주 승인번호 필요)"
        r = InvoiceFieldExtractor().extract(text)
        doc_type = next((f.value for f in r.fields if f.field_name == "document_type"), "")
        assert "견적" in doc_type
