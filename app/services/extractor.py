from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from app.confidence_thresholds import CLASSIFIER_SATURATION_SCORE, FIELD_CONFIDENCE_OK
from app.models import DocumentField, InvoiceLineItem, ValidationStatus
from app.schema import build_empty_fields

STANDARD_DATE_RE = re.compile(r"(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})")
KOREAN_DATE_RE = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
BIZ_RE = re.compile(r"(\d{3}[- ]?\d{2}[- ]?\d{5})")
AMOUNT_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{4,})(?!\d)")
COMPANY_RE = re.compile(
    r"(주식회사\s*[가-힣A-Za-z0-9]+|\(\s*주\s*\)\s*[가-힣A-Za-z0-9]+|㈜\s*[가-힣A-Za-z0-9]+|[가-힣A-Za-z0-9]+㈜|[가-힣A-Za-z0-9]+\(\s*주\s*\))"
)

DOCUMENT_LABELS = ["전자세금계산서", "세금계산서", "거래명세서", "명세표", "계약서", "견적서", "QUOTATION", "見積書"]
ISSUE_DATE_LABELS = ["작성일자", "작성 년월일", "작성년월일", "작성일", "발행일", "거래일자", "견적날짜"]
SUPPLIER_LABELS = ["공급자", "공급하는자", "공급하는 자", "seller", "발행자"]
RECEIPT_MERCHANT_LABELS = ["가맹점", "가맹점명", "상점명", "MERCHANT"]
BUYER_LABELS = ["공급받는자", "공급받는 자", "공급받는자", "청구처", "buyer", "수신처"]
SUPPLY_AMOUNT_LABELS = ["공급가액", "공급대가", "amount", "공급가액합계"]
TAX_AMOUNT_LABELS = ["세액", "부가세", "vat"]
TOTAL_AMOUNT_LABELS = ["합계", "총액", "청구금액", "청구합계", "합계금액", "최종합계", "total"]
ITEM_LABELS = ["품목", "품명", "서비스명", "서비스", "품목명", "견적항목", "견적 항목", "제품명", "item"]
REMARK_LABELS = [
    "비고", "참고", "추가내용", "추가 내용", "수정발급사유", "수정 발급 사유",
    "결제조건", "결제 조건", "결 제 조 건", "결재조건", "결재 조건",
    "특이사항", "특 이 사 항", "견적조건", "견적 조건", "인도장소", "인 도 장 소", "납기",
]


def _strip_spaces(value: str) -> str:
    """Collapse all whitespace so "결 제 조 건" matches "결제조건"."""
    return re.sub(r"\s+", "", value)


def _label_in_line(label: str, line: str) -> str | None:
    """Case-insensitive, whitespace-tolerant label lookup. Returns the matched substring
    in the ORIGINAL line (needed for later `re.sub`), or None.

    한글 라벨은 글자 사이 공백 허용(예: "결 제 조 건" → "결제조건").
    ASCII 전용 라벨은 단어 경계로 감싸 "item"이 "i am"에 걸리지 않게 함.
    """
    stripped_label = label.replace(" ", "")
    if not stripped_label:
        return None
    target = _strip_spaces(label).lower()
    compact = _strip_spaces(line).lower()
    if target not in compact:
        return None

    # 한글이 포함된 라벨: 글자 사이 공백 허용
    has_hangul = any("\uac00" <= ch <= "\ud7a3" for ch in stripped_label)
    if has_hangul:
        pattern = r"\s*".join(re.escape(ch) for ch in stripped_label)
    else:
        # ASCII 라벨은 단어 경계로 보호
        pattern = r"\b" + re.escape(stripped_label) + r"\b"
    match = re.search(pattern, line, flags=re.IGNORECASE)
    return match.group(0) if match else None


# ── Document type classifier signals ──────────────────────────────────────
# 유형별 증거 키워드/패턴. 각 항목: (kind, needle, weight)
# kind: "kw" (keyword) | "re" (regex) | "neg" (negative: 가중치만큼 점수 차감)
# weight: 1.0 = 약한 증거, 5.0+ = 결정적 증거
CLASSIFIER_SIGNALS: dict[str, list[tuple[str, str, float]]] = {
    "전자세금계산서": [
        ("kw", "전자세금계산서", 5.0),
        ("kw", "세금계산서", 3.0),
        ("kw", "수정세금계산서", 4.0),
        ("re", r"Tax\s*Invoice", 2.5),
        ("re", r"공급받는자.{0,40}사업자등록번호", 1.5),
        ("re", r"공급자.{0,40}사업자등록번호", 1.5),
        ("neg", "카드매출전표", 3.0),
        ("neg", "신용카드매출전표", 3.0),
        ("neg", "현금영수증", 3.0),
        ("neg", "견적서", 2.0),
    ],
    "거래명세서": [
        ("kw", "거래명세서", 5.0),
        ("kw", "거래명세표", 5.0),
        ("kw", "납품명세서", 4.0),
        ("kw", "납품서", 3.0),
        ("kw", "물품수령증", 3.0),
        ("kw", "delivery", 1.5),
        ("re", r"거\s*래\s*명\s*세\s*(?:서|표)", 3.0),
        ("neg", "전자세금계산서", 1.5),
        ("neg", "견적서", 2.0),
        ("neg", "계약서", 2.0),
    ],
    "외부용역계약서": [
        ("kw", "외부용역계약서", 5.0),
        ("kw", "용역계약서", 4.5),
        ("kw", "개발용역계약서", 5.0),
        ("kw", "계약서", 2.5),
        ("kw", "계약기간", 2.0),
        ("kw", "계약금액", 2.0),
        ("kw", "contract", 2.0),
        ("re", r"갑\s*[:은과]", 1.0),
        ("re", r"을\s*[:은과]", 1.0),
        ("neg", "견적서", 2.5),
        ("neg", "영수증", 2.0),
    ],
    "개발용역견적서": [
        ("kw", "개발용역견적서", 5.0),
        ("kw", "개발용역", 3.0),
        ("re", r"AUA\d{4}-\d+", 4.0),
        ("kw", "견적서", 1.5),
        ("kw", "견적번호", 1.5),
        ("kw", "견적일자", 1.5),
        ("neg", "계약서", 2.0),
        ("neg", "영수증", 2.0),
    ],
    "일반견적서": [
        ("kw", "QUOTATION", 4.0),
        ("kw", "見積書", 4.0),
        ("kw", "견적서", 3.0),
        ("kw", "견적번호", 1.5),
        ("kw", "견적일자", 1.5),
        ("kw", "유효기간", 1.0),
        ("re", r"Quote\s*No", 1.5),
        ("neg", "개발용역", 2.5),
        ("neg", "계약서", 2.5),
        ("neg", "영수증", 2.0),
    ],
    "영수증": [
        ("kw", "카드매출전표", 5.0),
        ("kw", "신용카드매출전표", 5.0),
        ("kw", "현금영수증", 5.0),
        ("kw", "간이영수증", 4.0),
        ("kw", "영수증", 3.0),
        ("kw", "가맹점", 2.5),
        ("kw", "승인번호", 2.0),
        ("kw", "POS", 1.0),
        ("re", r"\d{4}-?\d{2,4}-?\*{2,}", 2.5),
        ("re", r"\*{6,}", 1.0),
        ("neg", "전자세금계산서", 3.0),
        ("neg", "계약서", 2.0),
        ("neg", "견적서", 2.0),
    ],
}


def classify_text(text: str) -> tuple[str, float]:
    """Score-based document type classification.

    Returns (document_type, confidence) where confidence is 0.0~1.0.
    Empty string + 0.0 if no positive signals matched.

    Confidence blends two factors:
      1) coverage — positive score saturating at 5.0 (one decisive keyword)
      2) decisiveness — margin over 2nd place / best score
    """
    if not text:
        return "", 0.0

    compact = _strip_spaces(text).lower()
    # 정규식은 개행을 단일 공백으로 치환한 normalized에 대해 매칭 → OCR 라인 분리에 강건
    normalized = " ".join(text.split())
    scores: dict[str, float] = {}

    for doc_type, signals in CLASSIFIER_SIGNALS.items():
        positive = 0.0
        negative = 0.0
        for kind, needle, weight in signals:
            if kind == "neg":
                if _strip_spaces(needle).lower() in compact:
                    negative += weight
            elif kind == "kw":
                needle_clean = _strip_spaces(needle).lower()
                # ASCII 키워드(POS/contract/delivery 등)는 단어 경계로 보호해 내부 매칭 방지.
                # 한글 키워드는 공백 허용을 위해 compact(공백 제거) 기반 매칭 유지.
                if needle_clean.isascii():
                    if re.search(rf"\b{re.escape(needle_clean)}\b", normalized, flags=re.IGNORECASE):
                        positive += weight
                elif needle_clean in compact:
                    positive += weight
            elif kind == "re":
                if re.search(needle, normalized, flags=re.IGNORECASE):
                    positive += weight
        scores[doc_type] = positive - negative

    best_type = max(scores, key=lambda k: scores[k])
    best_score = scores[best_type]
    if best_score <= 0:
        return "", 0.0

    # CLASSIFIER_SATURATION_SCORE(=5.0) = 결정적 키워드 1개의 가중치 → 그 수준이면 coverage 포화(1.0)
    coverage = min(best_score / CLASSIFIER_SATURATION_SCORE, 1.0)
    ranked = sorted(scores.values(), reverse=True)
    runner_up = ranked[1] if len(ranked) > 1 else 0.0
    if runner_up <= 0:
        decisiveness = 1.0
    else:
        decisiveness = max(0.0, (best_score - runner_up) / best_score)
    confidence = round(coverage * (0.5 + 0.5 * decisiveness), 2)
    return best_type, min(confidence, 0.99)


@dataclass
class ExtractionOutcome:
    fields: list[DocumentField]
    items: list[InvoiceLineItem]
    document_confidence: float
    warnings: list[str]


@dataclass
class PartyInfo:
    name: str = ""
    biz_no: str = ""
    snippet: str = ""


class InvoiceFieldExtractor:
    def extract(self, text: str) -> ExtractionOutcome:
        """오케스트레이션만 수행. 실제 추출/평가는 private 헬퍼들로 분해."""
        cleaned = self._clean_text(text)
        normalized = " ".join(cleaned.split())
        compact = self._compact(cleaned)
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]

        document_type, _ = classify_text(cleaned)
        fields = {field.field_name: field for field in build_empty_fields(document_type)}
        warnings: list[str] = []

        self._populate_common_fields(fields, lines, cleaned, normalized, compact, document_type)
        if document_type == "영수증":
            self._populate_receipt_fields(fields, cleaned)

        document_confidence = self._finalize_metadata(fields, warnings)
        self._cross_validate_amounts(fields, warnings)
        items = self._extract_items(lines, fields)
        return ExtractionOutcome(
            fields=list(fields.values()), items=items,
            document_confidence=document_confidence, warnings=warnings,
        )

    def _populate_common_fields(
        self, fields: dict, lines: list[str], cleaned: str, normalized: str, compact: str, document_type: str,
    ) -> None:
        """모든 유형에 공통으로 적용되는 11개 필드 채우기."""
        issue_date = self._find_date(lines, cleaned, normalized, compact, document_type)
        supplier = self._extract_party(lines, cleaned, compact, document_type, role="supplier")
        buyer = self._extract_party(lines, cleaned, compact, document_type, role="buyer")
        amounts = self._extract_amounts(lines, cleaned, compact, document_type)
        item_name = self._extract_item_name(lines, cleaned, compact, document_type)
        remark = self._extract_remark(lines, cleaned, compact, document_type)

        self._pick_field(fields["document_type"], document_type, 0.98 if document_type else 0.0, normalized[:120])
        self._pick_field(fields["issue_date"], issue_date, 0.9 if issue_date else 0.0, issue_date or normalized[:120])
        self._pick_field(fields["supplier_name"], supplier.name, 0.88 if supplier.name else 0.0, supplier.snippet)
        self._pick_field(fields["supplier_biz_no"], supplier.biz_no, 0.92 if supplier.biz_no else 0.0, supplier.snippet)
        self._pick_field(fields["buyer_name"], buyer.name, 0.86 if buyer.name else 0.0, buyer.snippet)
        self._pick_field(fields["buyer_biz_no"], buyer.biz_no, 0.9 if buyer.biz_no else 0.0, buyer.snippet)
        self._pick_field(fields["supply_amount"], amounts["supply_amount"], 0.88 if amounts["supply_amount"] else 0.0, amounts["snippet"])
        self._pick_field(fields["tax_amount"], amounts["tax_amount"], 0.88 if amounts["tax_amount"] else 0.0, amounts["snippet"])
        self._pick_field(fields["total_amount"], amounts["total_amount"], 0.9 if amounts["total_amount"] else 0.0, amounts["snippet"])
        self._pick_field(fields["item_name"], item_name, 0.8 if item_name else 0.0, item_name or "")
        self._pick_field(fields["remark"], remark, 0.72 if remark else 0.0, remark or "")

    def _populate_receipt_fields(self, fields: dict, cleaned: str) -> None:
        """영수증 전용 필드 4개 (approval_no / transaction_time / card_number_masked / service_charge)."""
        approval_no = self._extract_approval_no(cleaned)
        transaction_time = self._extract_transaction_time(cleaned)
        card_masked = self._extract_card_number(cleaned)
        service_charge = self._extract_service_charge(cleaned)
        self._pick_field(fields["approval_no"], approval_no, 0.9 if approval_no else 0.0, approval_no or "")
        self._pick_field(fields["transaction_time"], transaction_time, 0.85 if transaction_time else 0.0, transaction_time or "")
        self._pick_field(fields["card_number_masked"], card_masked, 0.88 if card_masked else 0.0, card_masked or "")
        self._pick_field(fields["service_charge"], service_charge, 0.8 if service_charge else 0.0, service_charge or "")

    def _finalize_metadata(self, fields: dict, warnings: list[str]) -> float:
        """approval_status · confidence_score 채우고 필수 필드 누락 검증. 평균 신뢰도 반환."""
        self._pick_field(fields["approval_status"], "Draft", 1.0, "system")
        confidences = [field.confidence for field in fields.values() if field.value]
        document_confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
        self._pick_field(fields["confidence_score"], str(document_confidence), 1.0, "system")

        required_missing = [field.label for field in fields.values() if field.required and not field.value]
        if required_missing:
            warnings.append(f"필수 필드 누락: {', '.join(required_missing)}")
        return document_confidence

    def _clean_text(self, text: str) -> str:
        return text.replace("\xa0", " ").replace("\u3000", " ").replace("\r", "")

    def _compact(self, text: str) -> str:
        return re.sub(r"\s+", "", text)


    def _find_date(self, lines: list[str], text: str, normalized: str, compact: str, document_type: str) -> str:
        if document_type == "외부용역계약서":
            match = re.search(r"((20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일)\s*계약자", text)
            if match:
                return f"{match.group(2)}-{int(match.group(3)):02d}-{int(match.group(4)):02d}"

        if document_type == "개발용역견적서":
            month_day = re.search(r"견적날짜\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
            estimate_number = re.search(r"AUA(\d{2})(\d{2})-\d+", compact)
            if month_day and estimate_number:
                year = f"20{estimate_number.group(1)}"
                return f"{year}-{int(month_day.group(1)):02d}-{int(month_day.group(2)):02d}"
        if document_type == "일반견적서":
            quoted = self._normalize_date(text)
            if quoted:
                return quoted

        for line in lines:
            if any(label in line for label in ISSUE_DATE_LABELS):
                value = self._normalize_date(line)
                if value:
                    return value

        for source in (normalized, compact):
            value = self._normalize_date(source)
            if value:
                return value
        return ""

    def _normalize_date(self, text: str) -> str:
        match = STANDARD_DATE_RE.search(text)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        match = KOREAN_DATE_RE.search(text)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        return ""

    def _extract_party(self, lines: list[str], text: str, compact: str, document_type: str, role: str) -> PartyInfo:
        if document_type == "전자세금계산서":
            info = self._extract_tax_invoice_party(compact, role)
            if info.name or info.biz_no:
                return info

        if document_type == "외부용역계약서":
            info = self._extract_contract_party(text, role)
            if info.name:
                return info

        if document_type == "개발용역견적서":
            info = self._extract_estimate_party(text, role)
            if info.name:
                return info
        if document_type == "일반견적서":
            info = self._extract_general_quote_party(text, role)
            if info.name:
                return info

        if document_type == "거래명세서":
            info = self._extract_statement_party(text, role)
            if info.name:
                return info

        if document_type == "영수증" and role == "supplier":
            labels = list(RECEIPT_MERCHANT_LABELS) + list(SUPPLIER_LABELS)
            return self._extract_party_from_lines(lines, labels)

        labels = SUPPLIER_LABELS if role == "supplier" else BUYER_LABELS
        return self._extract_party_from_lines(lines, labels)

    def _extract_party_from_lines(self, lines: list[str], labels: list[str]) -> PartyInfo:
        found_name = ""
        found_biz_no = ""
        found_snippet = ""
        for line in lines:
            lower = line.lower()
            label = next((candidate for candidate in labels if candidate.lower() in lower), None)
            if not label:
                continue
            biz_match = BIZ_RE.search(line)
            biz_no = self._normalize_biz_no(biz_match.group(1)) if biz_match else ""
            candidate = re.sub(re.escape(label), "", line, count=1, flags=re.IGNORECASE)
            if biz_match:
                candidate = candidate.replace(biz_match.group(1), "", 1)
            candidate = re.sub(r"^[\s:()]+", "", candidate).strip()
            name = self._clean_company_name(candidate)

            if not found_name and name:
                found_name = name
                found_snippet = line[:140]
            if not found_biz_no and biz_no:
                found_biz_no = biz_no
                if not found_snippet:
                    found_snippet = line[:140]

            if found_name and found_biz_no:
                break
        return PartyInfo(name=found_name, biz_no=found_biz_no, snippet=found_snippet)

    def _extract_tax_invoice_party(self, compact: str, role: str) -> PartyInfo:
        biz_pattern = re.search(r"공급자등록번호(?P<sup>\d{3}-?\d{2}-?\d{5}).*?공급받는자등록번호(?P<buy>\d{3}-?\d{2}-?\d{5})", compact)
        name_matches = re.findall(r"상호\(법인명\)(.+?)성명", compact)
        supplier_name = self._clean_company_name(name_matches[0]) if len(name_matches) >= 1 else ""
        buyer_name = self._clean_company_name(name_matches[1]) if len(name_matches) >= 2 else ""
        if role == "supplier":
            return PartyInfo(
                name=supplier_name,
                biz_no=self._normalize_biz_no(biz_pattern.group("sup")) if biz_pattern else "",
                snippet="전자세금계산서 공급자",
            )
        return PartyInfo(
            name=buyer_name,
            biz_no=self._normalize_biz_no(biz_pattern.group("buy")) if biz_pattern else "",
            snippet="전자세금계산서 공급받는자",
        )

    def _extract_contract_party(self, text: str, role: str) -> PartyInfo:
        marker = "갑" if role == "supplier" else "을"
        pattern = rf"계약자\s*[\(\"“]*{marker}[\)\"”]*\s*[\n\r ]*상\s*호[:：]\s*(.+?)\n"
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            pattern = rf"계약자\s*[\(\"“]*{marker}[\)\"”]*.*?상\s*호[:：]\s*(.+?)\n"
            match = re.search(pattern, text, re.DOTALL)
        return PartyInfo(name=self._clean_company_name(match.group(1)) if match else "", snippet=f"계약자 {marker}")

    def _find_unique_companies(self, text: str) -> list[str]:
        """Extract unique company names from text using COMPANY_RE."""
        unique: list[str] = []
        for match in COMPANY_RE.finditer(text):
            name = self._clean_company_name(match.group(0))
            if name and name not in unique:
                unique.append(name)
        return unique

    def _find_supplier_excluding_buyer(self, companies: list[str], buyer_name: str, snippet: str) -> PartyInfo:
        """Return the first company that doesn't match the buyer name."""
        for company in companies:
            if buyer_name and buyer_name.replace(" ", "") in company.replace(" ", ""):
                continue
            return PartyInfo(name=company, snippet=snippet)
        return PartyInfo()

    def _find_estimate_buyer(self, text: str, unique: list[str]) -> PartyInfo:
        """Find buyer from estimate document via 수신처 label."""
        match = re.search(r"수\s*신\s*처\s*(.+?)(?:\n|$)", text)
        if match:
            candidate = match.group(1).strip()
            company_match = COMPANY_RE.search(candidate)
            name = self._clean_company_name(company_match.group(0)) if company_match else self._clean_company_name(candidate)
            if name:
                return PartyInfo(name=name, snippet="견적서 수신처")
        if len(unique) >= 2:
            return PartyInfo(name=unique[1], snippet="견적서 수신처 추정")
        return PartyInfo()

    def _extract_estimate_party(self, text: str, role: str) -> PartyInfo:
        unique = self._find_unique_companies(text)

        if role == "buyer":
            return self._find_estimate_buyer(text, unique)

        if role == "supplier" and unique:
            buyer_name = self._find_estimate_buyer(text, unique).name if re.search(r"수\s*신\s*처", text) else ""
            return self._find_supplier_excluding_buyer(unique, buyer_name, "견적서 발행자 추정")

        return PartyInfo()

    def _find_general_quote_buyer(self, text: str, unique: list[str]) -> PartyInfo:
        """Find buyer from general quote via 귀하 label."""
        match = re.search(r"([가-힣A-Za-z0-9()㈜\s]+?)\s*귀하", text)
        if match:
            return PartyInfo(name=self._clean_company_name(match.group(1).strip()), snippet="견적서 수신처")
        if unique:
            return PartyInfo(name=unique[-1], snippet="견적서 수신처 추정")
        return PartyInfo()

    def _extract_general_quote_party(self, text: str, role: str) -> PartyInfo:
        unique = self._find_unique_companies(text)

        if role == "buyer":
            return self._find_general_quote_buyer(text, unique)

        if role == "supplier" and unique:
            buyer_name = self._find_general_quote_buyer(text, unique).name if "귀하" in text else ""
            return self._find_supplier_excluding_buyer(unique, buyer_name, "견적서 발행자 추정")

        return PartyInfo()

    def _extract_statement_party(self, text: str, role: str) -> PartyInfo:
        unique = self._find_unique_companies(text)
        if not unique:
            return PartyInfo()

        # Identify buyer from buyer label sections
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        buyer_candidates: list[str] = []
        in_buyer_section = False
        for line in lines:
            matched_label = next((label for label in BUYER_LABELS if label in line), None)
            if matched_label:
                in_buyer_section = True
                after_label = line.split(matched_label, 1)[-1]
                company_match = COMPANY_RE.search(after_label)
                if company_match:
                    buyer_candidates.append(self._clean_company_name(company_match.group(0)))
                continue
            if in_buyer_section:
                company_match = COMPANY_RE.search(line)
                if company_match:
                    buyer_candidates.append(self._clean_company_name(company_match.group(0)))
                in_buyer_section = False

        if role == "buyer" and buyer_candidates:
            return PartyInfo(name=buyer_candidates[0], snippet="거래명세서 공급받는자")
        if role == "supplier":
            for company in unique:
                if company not in buyer_candidates:
                    return PartyInfo(name=company, snippet="거래명세서 공급자 추정")
            return PartyInfo(name=unique[0], snippet="거래명세서 공급자 추정")
        if role == "buyer" and len(unique) >= 2:
            return PartyInfo(name=unique[1], snippet="거래명세서 공급받는자 추정")
        return PartyInfo()

    def _extract_amounts(self, lines: list[str], text: str, compact: str, document_type: str) -> dict[str, str]:
        if document_type == "전자세금계산서":
            amounts = self._extract_tax_invoice_amounts(compact)
            if amounts["supply_amount"] and amounts["tax_amount"] and amounts["total_amount"]:
                return amounts

        if document_type == "개발용역견적서":
            amounts = self._extract_estimate_amounts(lines, text)
            if amounts["supply_amount"]:
                return amounts
        if document_type == "일반견적서":
            amounts = self._extract_general_quote_amounts(text)
            if amounts["supply_amount"]:
                return amounts

        if document_type == "외부용역계약서":
            amounts = self._extract_contract_amounts(text)
            if amounts["supply_amount"]:
                return amounts

        if document_type == "거래명세서":
            amounts = self._extract_amount_triplet_from_candidates(text)
            if any(amounts.values()):
                return amounts

        mapped = {"supply_amount": "", "tax_amount": "", "total_amount": "", "snippet": ""}
        patterns = {
            "supply_amount": SUPPLY_AMOUNT_LABELS,
            "tax_amount": TAX_AMOUNT_LABELS,
            "total_amount": TOTAL_AMOUNT_LABELS,
        }
        for line in lines:
            compact_line = self._compact(line).lower()
            for field_name, labels in patterns.items():
                if mapped[field_name]:
                    continue
                label = next((candidate for candidate in labels if candidate.lower().replace(" ", "") in compact_line), None)
                if not label:
                    continue
                match_label = re.search(re.escape(label), line, flags=re.IGNORECASE)
                if not match_label:
                    continue
                target = line[match_label.end() :]
                match = AMOUNT_RE.search(target)
                if match:
                    mapped[field_name] = match.group(1)
                    mapped["snippet"] = line[:140]

        fallback = self._extract_amount_triplet_from_candidates(text)
        if not mapped["supply_amount"]:
            mapped["supply_amount"] = fallback["supply_amount"]
        if not mapped["tax_amount"]:
            mapped["tax_amount"] = fallback["tax_amount"]
        if not mapped["total_amount"]:
            mapped["total_amount"] = fallback["total_amount"]
        if not mapped["snippet"]:
            mapped["snippet"] = fallback["snippet"]
        return mapped

    def _extract_tax_invoice_amounts(self, compact: str) -> dict[str, str]:
        mapped = {"supply_amount": "", "tax_amount": "", "total_amount": "", "snippet": "전자세금계산서 금액"}
        match = re.search(r"작성일자공급가액세액수정사유\d{4}/\d{2}/\d{2}(\d{1,3}(?:,\d{3})+)(\d{1,3}(?:,\d{3})+)", compact)
        if match:
            mapped["supply_amount"] = match.group(1)
            mapped["tax_amount"] = match.group(2)

        total_matches = re.findall(r"합계금액.*?(\d{1,3}(?:,\d{3})+)", compact)
        if total_matches:
            mapped["total_amount"] = total_matches[-1]

        if mapped["supply_amount"] and mapped["tax_amount"] and not mapped["total_amount"]:
            mapped["total_amount"] = self._sum_amounts(mapped["supply_amount"], mapped["tax_amount"])
        return mapped

    def _extract_estimate_amounts(self, lines: list[str], text: str) -> dict[str, str]:
        mapped = {"supply_amount": "", "tax_amount": "", "total_amount": "", "snippet": "견적서 금액"}
        all_amounts = [match.group(1) for match in AMOUNT_RE.finditer(text) if int(match.group(1).replace(",", "")) >= 10000]
        for line in lines:
            if "합계" in line:
                amount = AMOUNT_RE.search(line)
                if amount:
                    mapped["supply_amount"] = amount.group(1)
                    mapped["total_amount"] = amount.group(1)
                    return mapped
        if all_amounts:
            largest = max(all_amounts, key=lambda value: int(value.replace(",", "")))
            mapped["supply_amount"] = largest
            mapped["total_amount"] = largest
        return mapped

    def _extract_contract_amounts(self, text: str) -> dict[str, str]:
        mapped = {"supply_amount": "", "tax_amount": "", "total_amount": "", "snippet": "계약서 금액"}
        match = re.search(r"계약금액.*?(\d{1,3}(?:,\d{3})+)", text)
        if match:
            mapped["supply_amount"] = match.group(1)
            mapped["total_amount"] = match.group(1)
        return mapped

    def _extract_general_quote_amounts(self, text: str) -> dict[str, str]:
        # Reuse triplet detection with higher minimum threshold for quotes
        result = self._extract_amount_triplet_from_candidates(text, min_amount=100000)
        if result["supply_amount"]:
            result["snippet"] = "일반 견적서 금액"
        return result

    def _extract_amount_triplet_from_candidates(self, text: str, min_amount: int = 1000) -> dict[str, str]:
        mapped = {"supply_amount": "", "tax_amount": "", "total_amount": "", "snippet": text[:140]}
        candidates = []
        for match in AMOUNT_RE.finditer(text):
            raw = match.group(1)
            digits = int(raw.replace(",", ""))
            if digits < min_amount:
                continue
            candidates.append((raw, digits))

        if not candidates:
            return mapped

        unique_candidates: list[tuple[str, int]] = []
        for raw, digits in candidates:
            if (raw, digits) not in unique_candidates:
                unique_candidates.append((raw, digits))

        best_match: tuple[str, str, str] | None = None
        best_score = (-1, -1)
        for trio in itertools.combinations(unique_candidates, 3):
            for supply, tax, total in itertools.permutations(trio, 3):
                if supply[1] + tax[1] != total[1]:
                    continue
                vat_like = abs((supply[1] // 10) - tax[1]) <= max(1, supply[1] // 100)
                score = (
                    (2 if vat_like else 0) + (1 if supply[1] > tax[1] else 0),
                    total[1],
                )
                if score > best_score:
                    best_score = score
                    best_match = (supply[0], tax[0], total[0])
        if best_match:
            mapped["supply_amount"], mapped["tax_amount"], mapped["total_amount"] = best_match
            return mapped

        if len(unique_candidates) >= 3:
            mapped["supply_amount"] = unique_candidates[0][0]
            mapped["tax_amount"] = unique_candidates[1][0]
            mapped["total_amount"] = unique_candidates[2][0]
        return mapped

    def _extract_item_name(self, lines: list[str], text: str, compact: str, document_type: str) -> str:
        if document_type == "전자세금계산서":
            match = re.search(r"\d{2}\s*\d{2}\s+(.+?)\s+Set\s+1", text)
            if match:
                return match.group(1).strip()

        if document_type == "개발용역견적서":
            candidates: list[str] = []
            title_match = re.search(r"제목\s*[:：]?\s*(.+)", text)
            if title_match:
                candidates.append(self._clean_item_text(title_match.group(1)))
            multiline_title_match = re.search(
                r"제목\s*[:：]?\s*(.+?)\s*(?:전\s*화|유효기간|메\s*일|납\s*기)",
                text,
                re.DOTALL,
            )
            if multiline_title_match:
                candidates.append(self._clean_item_text(multiline_title_match.group(1)))
            match = re.search(r"품명\s+(.+?)\s+규격", text, re.DOTALL)
            if match:
                candidates.append(self._clean_item_text(match.group(1)))
            candidates = [candidate for candidate in candidates if candidate]
            if candidates:
                return max(candidates, key=len)

        if document_type == "일반견적서":
            notice_match = re.search(r"NOTICE\s*:\s*(.+?)\s*(?:見\s*積\s*書|\(QUOTATION\)|QUOTATION)", text, re.DOTALL)
            if notice_match:
                return self._clean_item_text(notice_match.group(1))
            match = re.search(r"PROJECT\s+(.+?)\s+1\s+₩", text, re.DOTALL)
            if match:
                return self._clean_item_text(match.group(1))

        if document_type == "외부용역계약서":
            match = re.search(r"계약건명\s+(.+)", text)
            if match:
                return match.group(1).strip()

        if document_type == "거래명세서":
            code_match = re.search(r"\b[A-Z]{1,8}\d[A-Z0-9-]*\b", text)
            if code_match:
                return code_match.group(0)

        header_tokens = {"수량", "단위", "단가", "금액", "소계", "금 액", "amount", "qty", "price"}
        skip_prefixes = ("합계", "합 계", "소계", "총 ", "total", "부가세")
        for idx, line in enumerate(lines):
            matched = next((m for m in (_label_in_line(lbl, line) for lbl in ITEM_LABELS) if m), None)
            if not matched:
                continue
            candidate = re.sub(re.escape(matched), "", line, count=1, flags=re.IGNORECASE)
            candidate = re.sub(r"^\s*\d+\.\s*", "", candidate)
            candidate = re.sub(r"^[\s:()]+", "", candidate).strip()
            compact_cand = _strip_spaces(candidate) if candidate else ""
            header_hits = sum(1 for tok in header_tokens if _strip_spaces(tok) in compact_cand) if compact_cand else 0
            is_header = not candidate or header_hits >= 2

            if is_header:
                # 테이블 헤더 행이면 다음 줄들에서 첫 데이터 행을 가져옴
                for next_line in lines[idx + 1:idx + 6]:
                    stripped = next_line.strip()
                    if not stripped or any(stripped.startswith(p) for p in skip_prefixes):
                        continue
                    # 첫 숫자(수량/금액) 토큰 이전까지만 품명으로 취함
                    tokens = re.split(r"\s+", stripped)
                    name_parts: list[str] = []
                    for tok in tokens:
                        if re.fullmatch(r"[\d.,₩\\\-]+원?", tok):
                            break
                        name_parts.append(tok)
                    candidate_name = " ".join(name_parts).strip()
                    if candidate_name and not re.fullmatch(r"[\d.,₩\\\-]+", candidate_name):
                        return self._clean_item_text(candidate_name[:80])
                continue
            return self._clean_item_text(candidate)
        return ""

    def _extract_remark(self, lines: list[str], text: str, compact: str, document_type: str) -> str:
        collected: list[str] = []
        for line in lines:
            matched = next((m for m in (_label_in_line(lbl, line) for lbl in REMARK_LABELS) if m), None)
            if not matched:
                continue
            candidate = re.sub(re.escape(matched), "", line, count=1, flags=re.IGNORECASE)
            candidate = re.sub(r"^\s*\d+\.\s*", "", candidate)
            candidate = re.sub(r"^[\s:()]+", "", candidate).strip()
            if candidate:
                collected.append(candidate[:120])
        if collected:
            # 여러 조건이 한 문서에 있을 때 우선순위: 결제/결재 조건 > 일반 비고
            priority = [c for c in collected if "결제" in c or "결재" in c or "정기" in c]
            return priority[0] if priority else collected[0]

        if document_type == "거래명세서" and "스캔풍 거래명세서" in text:
            return "스캔풍 거래명세서"
        if document_type == "전자세금계산서" and "영수 처리" in text:
            return "영수 처리"
        return ""

    def _extract_items(self, lines: list[str], fields: dict[str, DocumentField]) -> list[InvoiceLineItem]:
        items: list[InvoiceLineItem] = []
        for line in lines:
            if not any(_label_in_line(label, line) for label in ITEM_LABELS):
                continue
            candidate = self._extract_item_name([line], line, self._compact(line), "")
            if candidate:
                items.append(
                    InvoiceLineItem(
                        line_number=len(items) + 1,
                        item_name=candidate,
                        supply_amount=fields["supply_amount"].value,
                        tax_amount=fields["tax_amount"].value,
                    )
                )
        if items:
            return items
        if fields["item_name"].value or fields["supply_amount"].value or fields["tax_amount"].value:
            return [
                InvoiceLineItem(
                    line_number=1,
                    item_name=fields["item_name"].value,
                    supply_amount=fields["supply_amount"].value,
                    tax_amount=fields["tax_amount"].value,
                )
            ]
        return []

    def _pick_field(self, field: DocumentField, value: str, confidence: float, snippet: str) -> None:
        if value:
            field.value = value
            field.confidence = round(confidence, 2)
            field.source_snippet = snippet[:140]
            field.validation_status = ValidationStatus.OK if confidence >= FIELD_CONFIDENCE_OK else ValidationStatus.WARNING
        elif field.required:
            field.validation_status = ValidationStatus.MISSING
        else:
            field.validation_status = ValidationStatus.WARNING

    def _cross_validate_amounts(self, fields: dict[str, DocumentField], warnings: list[str]) -> None:
        try:
            supply = int(fields["supply_amount"].value.replace(",", "")) if fields["supply_amount"].value else None
            tax = int(fields["tax_amount"].value.replace(",", "")) if fields["tax_amount"].value else None
            total = int(fields["total_amount"].value.replace(",", "")) if fields["total_amount"].value else None
        except ValueError:
            warnings.append("금액 필드 정규화에 실패했습니다.")
            return

        if supply is not None and tax is not None and total is not None and supply + tax != total:
            fields["total_amount"].validation_status = ValidationStatus.WARNING
            warnings.append("합계금액이 공급가액과 세액의 합과 일치하지 않습니다.")

    def _normalize_biz_no(self, value: str) -> str:
        digits = re.sub(r"\D", "", value)
        if len(digits) != 10:
            return value
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"

    def _clean_company_name(self, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value).strip()
        cleaned = re.sub(r"^(?:0+\s*)+", "", cleaned)
        cleaned = re.sub(r"^[^0-9A-Za-z\uac00-\ud7a3()\u3231]+", "", cleaned)
        cleaned = re.sub(
            r"^(?:\uc218\s*\uc2e0\s*\ucc98|\uadc0\s*\ud558|\ub2f4\s*\ub2f9\s*\uc790|\ub2f4\s*\ub2f9)\s*",
            "",
            cleaned,
        )
        for prefix in ("\uc218\uc2e0\ucc98", "\uadc0\ud558", "\ub2f4\ub2f9\uc790", "\ub2f4\ub2f9"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
        cleaned = cleaned.replace("(\u0020\uc8fc\u0020)", "(\uc8fc)")
        cleaned = cleaned.replace("(\uc8fc\u0020)", "(\uc8fc)")
        cleaned = cleaned.replace("\u3231 ", "\u3231")
        return cleaned[:120]

    def _clean_item_text(self, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value).strip()
        cleaned = re.sub(r"^(?:품명|제목|NOTICE)\s*[:：]?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.replace(" ,", ",")
        return cleaned[:120]

    def _sum_amounts(self, left: str, right: str) -> str:
        total = int(left.replace(",", "")) + int(right.replace(",", ""))
        return f"{total:,}"

    # ── 영수증 전용 필드 추출 ────────────────────────────────────────────
    def _extract_approval_no(self, text: str) -> str:
        """승인번호: 8~14자리 숫자 (공백/하이픈 포함 가능). 라벨 기반 우선."""
        # 라벨 기반: "승인번호: 12345678"
        label_match = re.search(r"승\s*인\s*번\s*호[\s:]*([0-9\s\-]{8,20})", text)
        if label_match:
            digits = re.sub(r"[\s\-]", "", label_match.group(1))
            if 8 <= len(digits) <= 14:
                return digits
        # 카드매출전표 일반 패턴: 근접 숫자 8-12자리
        for line in text.splitlines():
            if "승인" in line:
                m = re.search(r"(\d{8,14})", line)
                if m:
                    return m.group(1)
        return ""

    def _extract_transaction_time(self, text: str) -> str:
        """거래일시: YYYY-MM-DD HH:MM(:SS) 또는 24시 형식."""
        # Full datetime: 2024-04-20 14:30:15 or 2024/04/20 14:30
        m = re.search(
            r"(20\d{2})[./\-](\d{1,2})[./\-](\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?",
            text,
        )
        if m:
            y, mo, d, hh, mm = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
            ss = int(m.group(6)) if m.group(6) else 0
            return f"{y}-{mo:02d}-{d:02d} {hh:02d}:{mm:02d}:{ss:02d}"
        # 거래일시 label + time only
        label_match = re.search(r"거\s*래\s*일\s*시[\s:]*([\d\s:./\-]+)", text)
        if label_match:
            return label_match.group(1).strip()[:30]
        return ""

    def _extract_card_number(self, text: str) -> str:
        """카드번호 마스킹: 1234-**-****-5678 또는 유사 형식."""
        # 마스킹 형식 우선
        m = re.search(r"(\d{4}[\s\-]?\*{2,}[\s\-\*]*\d{2,4})", text)
        if m:
            return re.sub(r"\s+", "", m.group(1))
        # 라벨 기반
        label_match = re.search(r"카\s*드\s*번\s*호[\s:]*([0-9*\-\s]{10,30})", text)
        if label_match:
            return re.sub(r"\s+", "", label_match.group(1).strip())
        return ""

    def _extract_service_charge(self, text: str) -> str:
        """봉사료 (service charge)."""
        m = re.search(r"봉\s*사\s*료[\s:]*([\d,]+)", text)
        if m:
            return m.group(1)
        return ""
