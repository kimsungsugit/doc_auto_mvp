from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ai_structurer import OpenAIStructurer
from app.services.extractor import InvoiceFieldExtractor


SAMPLES_PATH = ROOT / "samples" / "sample_cases.json"
REPORTS_DIR = ROOT / "reports"


def evaluate_case(case: dict, mode: str, structurer: OpenAIStructurer | None) -> dict:
    extractor = InvoiceFieldExtractor()
    text = "\n".join(case["lines"])

    if mode == "ai_first" and structurer:
        doc_type, confidence, ai_fields = structurer.classify_and_extract(text)
        if ai_fields:
            field_map = {field.field_name: field.value for field in ai_fields}
            field_map["_ai_doc_type"] = doc_type
            field_map["_ai_confidence"] = str(confidence)
        else:
            # AI failed, fall back to rule
            extraction = extractor.extract(text)
            field_map = {field.field_name: field.value for field in extraction.fields}
            field_map["_ai_fallback"] = "true"
    elif mode == "openai_refine" and structurer:
        extraction = extractor.extract(text)
        refined = structurer.maybe_refine(text, deepcopy(extraction.fields))
        field_map = {field.field_name: field.value for field in refined}
    else:
        extraction = extractor.extract(text)
        field_map = {field.field_name: field.value for field in extraction.fields}

    expected = case["expected"]
    matches = {key: field_map.get(key, "") == value for key, value in expected.items()}
    hit_count = sum(1 for matched in matches.values() if matched)
    return {
        "file_name": case["file_name"],
        "expected": expected,
        "actual": {key: field_map.get(key, "") for key in expected.keys()},
        "matches": matches,
        "score": {
            "matched": hit_count,
            "total": len(expected),
            "ratio": round(hit_count / len(expected), 2) if expected else 1.0,
        },
    }


def build_report(mode: str) -> dict:
    cases = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    structurer = None
    if mode in ("ai_first", "openai_refine"):
        os.environ["OPENAI_STRUCTURING_ENABLED"] = "true"
        structurer = OpenAIStructurer()

    case_results = [evaluate_case(case, mode=mode, structurer=structurer) for case in cases]
    matched = sum(item["score"]["matched"] for item in case_results)
    total = sum(item["score"]["total"] for item in case_results)
    return {
        "mode": mode,
        "model": os.getenv("OPENAI_STRUCTURING_MODEL", "gpt-4o-mini") if structurer else None,
        "summary": {
            "matched": matched,
            "total": total,
            "ratio": round(matched / total, 2) if total else 1.0,
        },
        "cases": case_results,
    }


def build_report_paths(report: dict) -> tuple[Path, Path]:
    stem = f"sample_eval_{report['mode']}"
    return REPORTS_DIR / f"{stem}.json", REPORTS_DIR / f"{stem}.md"


def write_reports(report: dict) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_report_path, md_report_path = build_report_paths(report)
    json_report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 샘플 평가 리포트 ({report['mode']})",
        "",
        f"- model: {report['model'] or 'n/a'}",
        f"- matched: {report['summary']['matched']}/{report['summary']['total']}",
        f"- ratio: {report['summary']['ratio']}",
        "",
        "| 파일 | 일치 수 | 전체 | 비율 |",
        "|---|---:|---:|---:|",
    ]
    for item in report["cases"]:
        lines.append(
            f"| {item['file_name']} | {item['score']['matched']} | {item['score']['total']} | {item['score']['ratio']} |"
        )

    # Show field-level detail for mismatches
    mismatches = [item for item in report["cases"] if item["score"]["ratio"] < 1.0]
    if mismatches:
        lines.extend(["", "## 불일치 상세", ""])
        for item in mismatches:
            lines.append(f"### {item['file_name']}")
            lines.append("| 필드 | 기대값 | 실제값 | 일치 |")
            lines.append("|---|---|---|:---:|")
            for key in item["expected"]:
                mark = "O" if item["matches"][key] else "X"
                lines.append(f"| {key} | {item['expected'][key]} | {item['actual'].get(key, '')} | {mark} |")
            lines.append("")

    md_report_path.write_text("\n".join(lines), encoding="utf-8")
    return json_report_path, md_report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="샘플 케이스 평가")
    parser.add_argument(
        "--mode",
        choices=["rule_only", "ai_first", "openai_refine"],
        default="rule_only",
        help="평가 모드: rule_only (규칙만), ai_first (AI 분류+추출), openai_refine (규칙+AI 보정)",
    )
    args = parser.parse_args()

    report = build_report(mode=args.mode)
    json_report_path, md_report_path = write_reports(report)
    print(f"JSON: {json_report_path}")
    print(f"MD:   {md_report_path}")
    print(f"Score: {report['summary']['matched']}/{report['summary']['total']} ({report['summary']['ratio']})")


if __name__ == "__main__":
    main()
