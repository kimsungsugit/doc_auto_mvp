from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORTS_DIR = ROOT / "reports"
RULE_REPORT_PATH = REPORTS_DIR / "sample_eval_rule_only.json"
AI_REPORT_PATH = REPORTS_DIR / "sample_eval_gpt_5_4_mini.json"
COMPARISON_JSON_PATH = REPORTS_DIR / "sample_eval_comparison.json"
COMPARISON_MD_PATH = REPORTS_DIR / "sample_eval_comparison.md"


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_comparison(rule_report: dict, ai_report: dict) -> dict:
    rule_cases = {case["file_name"]: case for case in rule_report["cases"]}
    ai_cases = {case["file_name"]: case for case in ai_report["cases"]}
    case_comparisons: list[dict] = []

    for file_name, rule_case in rule_cases.items():
        ai_case = ai_cases[file_name]
        improved_fields = [
            field_name
            for field_name, matched in ai_case["matches"].items()
            if matched and not rule_case["matches"].get(field_name, False)
        ]
        regressed_fields = [
            field_name
            for field_name, matched in ai_case["matches"].items()
            if not matched and rule_case["matches"].get(field_name, False)
        ]
        delta = ai_case["score"]["matched"] - rule_case["score"]["matched"]
        case_comparisons.append(
            {
                "file_name": file_name,
                "rule_score": rule_case["score"],
                "ai_score": ai_case["score"],
                "delta": delta,
                "improved_fields": improved_fields,
                "regressed_fields": regressed_fields,
            }
        )

    case_comparisons.sort(key=lambda item: (-item["delta"], item["file_name"]))
    improved_cases = [item for item in case_comparisons if item["delta"] > 0]
    regressed_cases = [item for item in case_comparisons if item["delta"] < 0]

    return {
        "rule_mode": rule_report["mode"],
        "ai_mode": ai_report["mode"],
        "ai_model": ai_report["model"],
        "summary": {
            "rule_ratio": rule_report["summary"]["ratio"],
            "ai_ratio": ai_report["summary"]["ratio"],
            "rule_matched": rule_report["summary"]["matched"],
            "ai_matched": ai_report["summary"]["matched"],
            "total_fields": rule_report["summary"]["total"],
            "delta": ai_report["summary"]["matched"] - rule_report["summary"]["matched"],
            "improved_case_count": len(improved_cases),
            "regressed_case_count": len(regressed_cases),
        },
        "cases": case_comparisons,
    }


def write_comparison(comparison: dict) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    COMPARISON_JSON_PATH.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 샘플 비교 리포트",
        "",
        f"- rule-only: {comparison['summary']['rule_matched']}/{comparison['summary']['total_fields']} ({comparison['summary']['rule_ratio']})",
        f"- openai-mini: {comparison['summary']['ai_matched']}/{comparison['summary']['total_fields']} ({comparison['summary']['ai_ratio']})",
        f"- delta: +{comparison['summary']['delta']}",
        f"- improved cases: {comparison['summary']['improved_case_count']}",
        f"- regressed cases: {comparison['summary']['regressed_case_count']}",
        f"- model: {comparison['ai_model']}",
        "",
        "| 파일 | Rule | AI Mini | Delta | 개선 필드 |",
        "|---|---:|---:|---:|---|",
    ]

    for case in comparison["cases"]:
        improved_fields = ", ".join(case["improved_fields"]) if case["improved_fields"] else "-"
        lines.append(
            f"| {case['file_name']} | {case['rule_score']['matched']}/{case['rule_score']['total']} | "
            f"{case['ai_score']['matched']}/{case['ai_score']['total']} | {case['delta']} | {improved_fields} |"
        )

    COMPARISON_MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    return COMPARISON_JSON_PATH, COMPARISON_MD_PATH


def main() -> None:
    comparison = build_comparison(load_report(RULE_REPORT_PATH), load_report(AI_REPORT_PATH))
    json_path, md_path = write_comparison(comparison)
    print(json_path)
    print(md_path)
    print(comparison["summary"])


if __name__ == "__main__":
    main()
