from __future__ import annotations

from app.services.extractor import classify_text


class TestClassifierBasicTypes:
    """각 문서 유형의 대표 텍스트가 정확히 분류되어야 함."""

    def test_tax_invoice(self):
        text = """
        전자세금계산서
        공급자 사업자등록번호 123-45-67890
        공급받는자 사업자등록번호 234-56-78901
        공급가액 1,000,000  세액 100,000  합계 1,100,000
        """
        doc_type, confidence = classify_text(text)
        assert doc_type == "전자세금계산서"
        assert confidence >= 0.5

    def test_transaction_statement(self):
        text = """
        거래명세서
        공급자 (주)홍길동상사
        품목  수량  단가  공급가액
        연필  100   500   50,000
        """
        doc_type, confidence = classify_text(text)
        assert doc_type == "거래명세서"
        assert confidence >= 0.5

    def test_service_contract(self):
        text = """
        외부용역계약서
        갑: (주)발주사  을: (주)수행사
        계약기간: 2024-01-01 ~ 2024-12-31
        계약금액: 100,000,000원
        """
        doc_type, confidence = classify_text(text)
        assert doc_type == "외부용역계약서"
        assert confidence >= 0.5

    def test_dev_quotation(self):
        text = """
        개발용역견적서
        견적번호: AUA2401-5
        개발용역 프로젝트: 웹시스템 구축
        견적금액 50,000,000
        """
        doc_type, confidence = classify_text(text)
        assert doc_type == "개발용역견적서"
        assert confidence >= 0.5

    def test_general_quotation(self):
        text = """
        QUOTATION
        견적번호: Q-2024-001
        유효기간: 30일
        총 견적가: 5,000,000
        """
        doc_type, confidence = classify_text(text)
        assert doc_type == "일반견적서"
        assert confidence >= 0.5

    def test_receipt_card_slip(self):
        text = """
        신용카드매출전표
        가맹점: 홍길동식당
        승인번호: 12345678
        카드번호: 1234-56**-****-7890
        합계 15,000
        """
        doc_type, confidence = classify_text(text)
        assert doc_type == "영수증"
        assert confidence >= 0.5

    def test_cash_receipt(self):
        text = """
        현금영수증
        가맹점 사업자번호 123-45-67890
        승인번호 98765432
        총액 10,000
        """
        doc_type, _ = classify_text(text)
        assert doc_type == "영수증"


class TestClassifierAmbiguousCases:
    """혼동을 일으킬 수 있는 텍스트에서 올바른 유형 선택."""

    def test_receipt_wins_over_tax_invoice_when_no_tax_header(self):
        """영수증 키워드가 강하고 세금계산서는 '영수증' 단어만 나올 때 → 영수증."""
        text = "카드매출전표 승인번호 12345 가맹점 ABC 카드번호 1234-56**-****-1234"
        doc_type, _ = classify_text(text)
        assert doc_type == "영수증"

    def test_tax_invoice_wins_when_both_parties_labeled(self):
        """전자세금계산서 제목이 있고 공급자/공급받는자 둘 다 있으면 → 전자세금계산서."""
        text = """
        전자세금계산서
        공급자 사업자등록번호 111-11-11111
        공급받는자 사업자등록번호 222-22-22222
        """
        doc_type, _ = classify_text(text)
        assert doc_type == "전자세금계산서"

    def test_quotation_vs_contract(self):
        """견적서 키워드 + 계약기간 없음 → 견적서 (계약서 아님)."""
        text = "QUOTATION 견적번호 Q-1 유효기간 30일 총 견적가 1,000,000"
        doc_type, _ = classify_text(text)
        assert doc_type == "일반견적서"

    def test_contract_vs_quotation(self):
        """계약서 키워드 + 계약기간/계약금액 → 계약서 (견적서 아님)."""
        text = "외부용역계약서 갑: A사 을: B사 계약기간 2024-01-01 ~ 2024-12-31 계약금액 1,000,000"
        doc_type, _ = classify_text(text)
        assert doc_type == "외부용역계약서"

    def test_dev_quote_vs_general_quote(self):
        """개발용역 컨텍스트 + 견적서 → 개발용역견적서."""
        text = "개발용역견적서 견적번호 AUA2401-3 개발 프로젝트 견적금액 10,000,000"
        doc_type, _ = classify_text(text)
        assert doc_type == "개발용역견적서"

    def test_delivery_statement_variant(self):
        """납품명세서 → 거래명세서 스키마로 분류."""
        text = "납품명세서 공급자 A상사 품목 연필 100개 50,000원"
        doc_type, _ = classify_text(text)
        assert doc_type == "거래명세서"


class TestClassifierRobustness:
    """OCR로 생긴 공백/노이즈 허용, 빈 텍스트 처리."""

    def test_spaced_korean_title(self):
        """OCR이 글자 사이 공백을 넣어도 인식되어야 함."""
        text = "전 자 세 금 계 산 서 공급자 사업자등록번호 111-11-11111"
        doc_type, _ = classify_text(text)
        assert doc_type == "전자세금계산서"

    def test_spaced_receipt_title(self):
        text = "신 용 카 드 매 출 전 표 가맹점 ABC 승인번호 12345"
        doc_type, _ = classify_text(text)
        assert doc_type == "영수증"

    def test_empty_text_returns_empty(self):
        doc_type, confidence = classify_text("")
        assert doc_type == ""
        assert confidence == 0.0

    def test_pure_noise_returns_empty(self):
        """유형 신호가 전혀 없는 텍스트는 빈 문자열."""
        text = "abc def 123 some random text without any document markers"
        doc_type, confidence = classify_text(text)
        assert doc_type == ""
        assert confidence == 0.0

    def test_regex_matches_across_newlines(self):
        """OCR이 '공급자'와 '사업자등록번호'를 다른 줄에 분리해도 정규식이 매칭되어야 함."""
        text = """전자세금계산서
공급자
사업자등록번호 111-11-11111
공급받는자
사업자등록번호 222-22-22222"""
        doc_type, confidence = classify_text(text)
        assert doc_type == "전자세금계산서"
        # 정규식 2개(공급자/공급받는자 + 사업자등록번호) 매칭돼서 약한 케이스보다 conf 높아야 함
        weak_text = "세금계산서"
        _, weak_conf = classify_text(weak_text)
        assert confidence > weak_conf

    def test_ascii_keyword_not_matched_as_substring(self):
        """'POS'가 'postpaid' 같은 단어 내부에 걸려 영수증으로 오분류되지 않아야 함."""
        text = "개발용역견적서 견적번호 AUA2401-5 postpaid service 견적금액 1,000,000"
        doc_type, _ = classify_text(text)
        # 'POS'가 'postpaid' 내부에 걸려서 영수증 점수가 올라가면 안 됨
        assert doc_type == "개발용역견적서"

    def test_contract_keyword_not_matched_inside_contractor(self):
        """'contract'가 'contractor' 안에 걸려 계약서로 오분류되지 않아야 함."""
        text = "QUOTATION 견적번호 Q-1 subcontractor list attached 총액 1,000,000"
        doc_type, _ = classify_text(text)
        assert doc_type == "일반견적서"


class TestClassifierConfidenceBehavior:
    """Confidence 값의 타당성 — 강한 신호 > 약한 신호."""

    def test_strong_signal_higher_confidence_than_weak(self):
        """결정적 키워드(카드매출전표)가 있는 쪽이 약한 키워드(영수증)만 있는 쪽보다 conf 높음."""
        strong_text = "신용카드매출전표 가맹점 ABC 승인번호 12345 카드번호 1234-56**-****"
        weak_text = "영수증 금액 15,000"
        _, strong_conf = classify_text(strong_text)
        _, weak_conf = classify_text(weak_text)
        assert strong_conf > weak_conf

    def test_decisiveness_penalty_when_second_place_close(self):
        """2위와 점수 차가 작으면 conf에 페널티가 적용되어야 함."""
        # 세금계산서 + 견적서 키워드 둘 다 있는 모호 케이스
        ambiguous = "세금계산서 견적서 견적번호 Q-1"
        _, amb_conf = classify_text(ambiguous)
        # 깔끔한 케이스
        clean = "전자세금계산서 공급자 사업자등록번호 111-11-11111 공급받는자 사업자등록번호 222-22-22222"
        _, clean_conf = classify_text(clean)
        assert clean_conf > amb_conf
