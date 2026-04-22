from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ai_structurer import OpenAIStructurer
from app.services.extractor import InvoiceFieldExtractor
from app.services.pdf_text import PdfTextService


REPORT_JSON = ROOT / "reports" / "real_doc_eval_latest.json"
REPORT_MD = ROOT / "reports" / "real_doc_eval_latest.md"

COMPARE_FIELDS = [
    "document_type", "issue_date", "supplier_name", "buyer_name",
    "supply_amount", "tax_amount", "total_amount", "item_name",
]


def collect_targets() -> list[Path]:
    candidates = [
        ROOT / "samples" / "외부용역계약서_오아시스템.pdf",
        ROOT / "samples" / "AUA2505-116(현보-MCU다운로드_개발용역).pdf",
        ROOT / "samples" / "MCU다운로드툴개발_외주용역비_세금계산서.pdf",
        ROOT / "samples" / "거래명세표(현보_WN04A_0528).pdf",
        ROOT / "samples" / "EOL 테스터 견적 260225.pdf",
        ROOT / "samples" / "tmp_eval" / "doc_01_contract.pdf",
        ROOT / "samples" / "tmp_eval" / "doc_02_service_order.pdf",
        ROOT / "samples" / "tmp_eval" / "doc_03_tax_invoice.pdf",
        ROOT / "samples" / "tmp_eval" / "doc_04_statement.pdf",
        ROOT / "samples" / "tmp_eval" / "doc_05_eol_estimate.pdf",
    ]
    return [path for path in candidates if path.exists()]


def evaluate_document(path: Path, pdf_service: PdfTextService, extractor: InvoiceFieldExtractor, structurer: OpenAIStructurer | None) -> dict:
    extraction = pdf_service.extract(path)
    text = extraction.text

    # Rule-based extraction
    rule_parsed = extractor.extract(text)
    rule_fields = {f.field_name: f.value for f in rule_parsed.fields}

    # AI-First extraction (if available)
    ai_fields: dict[str, str] = {}
    ai_doc_type = ""
    ai_confidence = 0.0
    if structurer and structurer.enabled:
        ai_doc_type, ai_confidence, ai_field_list = structurer.classify_and_extract(text)
        if ai_field_list:
            ai_fields = {f.field_name: f.value for f in ai_field_list}

    # Build comparison
    comparison: list[dict] = []
    for field_name in COMPARE_FIELDS:
        rule_val = rule_fields.get(field_name, "")
        ai_val = ai_fields.get(field_name, "")
        comparison.append({
            "field": field_name,
            "rule": rule_val,
            "ai": ai_val,
            "match": rule_val == ai_val if ai_val else None,
        })

    return {
        "file_name": path.name,
        "requires_ocr": extraction.requires_ocr,
        "warnings": extraction.warnings + rule_parsed.warnings,
        "rule_fields": {k: rule_fields.get(k, "") for k in COMPARE_FIELDS},
        "ai_fields": {k: ai_fields.get(k, "") for k in COMPARE_FIELDS} if ai_fields else None,
        "ai_doc_type": ai_doc_type,
        "ai_confidence": ai_confidence,
        "comparison": comparison,
    }


def build_markdown(results: list[dict], ai_available: bool) -> str:
    lines = [
        "# 실문서 평가 요약",
        "",
        f"- 문서 수: {len(results)}",
        f"- AI 비교: {'활성' if ai_available else '비활성 (OPENAI_STRUCTURING_ENABLED=true 필요)'}",
        "",
    ]
    for item in results:
        lines.extend([
            f"## {item['file_name']}",
            f"- OCR 필요: {'예' if item['requires_ocr'] else '아니오'}",
        ])
        if item["ai_doc_type"]:
            lines.append(f"- AI 분류: {item['ai_doc_type']} (confidence: {item['ai_confidence']:.2f})")
        lines.append("")

        if ai_available and item["ai_fields"]:
            lines.append("| 필드 | 규칙 기반 | AI 추출 | 일치 |")
            lines.append("|---|---|---|:---:|")
            for comp in item["comparison"]:
                match_mark = "-" if comp["match"] is None else ("O" if comp["match"] else "**X**")
                lines.append(f"| {comp['field']} | {comp['rule']} | {comp['ai']} | {match_mark} |")
        else:
            lines.append("| 필드 | 값 |")
            lines.append("|---|---|")
            for field_name in COMPARE_FIELDS:
                lines.append(f"| {field_name} | {item['rule_fields'].get(field_name, '')} |")

        warnings = item.get("warnings", [])
        if warnings:
            lines.append(f"\n> 경고: {' / '.join(warnings)}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    pdf_service = PdfTextService()
    extractor = InvoiceFieldExtractor()

    ai_available = os.getenv("OPENAI_STRUCTURING_ENABLED", "").lower() in {"1", "true", "yes"}
    structurer = OpenAIStructurer() if ai_available else None

    targets = collect_targets()
    if not targets:
        print("평가 대상 PDF 파일이 없습니다.")
        return 1

    results = [evaluate_document(path, pdf_service, extractor, structurer) for path in targets]

    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(build_markdown(results, ai_available), encoding="utf-8")

    print(f"JSON: {REPORT_JSON}")
    print(f"MD:   {REPORT_MD}")
    print(f"문서 수: {len(results)}, AI 비교: {'활성' if ai_available else '비활성'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
