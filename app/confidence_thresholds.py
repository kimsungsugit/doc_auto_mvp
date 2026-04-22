"""문서 추출·분류·라우팅에 쓰이는 신뢰도 임계값 중앙 관리.

코드 곳곳에 흩어져 있던 매직 넘버(0.95/0.80/0.85/0.6/0.1/0.75/5.0)를 한 곳에 모아,
운영자가 환경변수로 조정 가능하도록 함. 기본값은 기존 동작과 동일.

튜닝 가이드:
- 자동 승인 비율을 높이고 싶다 → CONFIDENCE_AUTO_APPROVE 낮춤 (0.95 → 0.90)
- 고우선 검수가 너무 많다 → CONFIDENCE_REVIEW_NORMAL 낮춤 (0.80 → 0.70)
- AI가 규칙을 너무 자주 이기면 → CONFIDENCE_AI_TRUSTED 올림 (0.85 → 0.90)
- 규칙 분류기가 과잉 trusted되면 → CONFIDENCE_RULE_TRUSTED 올림 (0.60 → 0.75)

값 변경 시 반드시 tests/test_classifier.py / test_workflow_branches.py 영향 확인.
"""
from __future__ import annotations

import os


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# ── 신뢰도 라우팅 (workflow._apply_confidence_routing) ──────────────────────
# avg_confidence ≥ 이 값 + 필수필드 완비 + 금액검증 → REVIEWED (자동 승인)
CONFIDENCE_AUTO_APPROVE = _env_float("CONFIDENCE_AUTO_APPROVE", 0.95)
# avg_confidence < 이 값 → NEEDS_REVIEW + review_priority=high
# 이상이면 review_priority=normal
CONFIDENCE_REVIEW_NORMAL = _env_float("CONFIDENCE_REVIEW_NORMAL", 0.80)

# ── AI vs 규칙 병합 (workflow._merge_classifications) ──────────────────────
# AI conf가 이 값 이상이면 규칙보다 우선 선택 가능 (단, margin 조건 추가 충족 시)
CONFIDENCE_AI_TRUSTED = _env_float("CONFIDENCE_AI_TRUSTED", 0.85)
# AI가 규칙을 이기려면 (AI conf - 규칙 conf) ≥ 이 margin 이어야 함
CONFIDENCE_AI_MARGIN_OVER_RULE = _env_float("CONFIDENCE_AI_MARGIN_OVER_RULE", 0.10)
# 규칙 conf가 이 값 이상 ∧ AI conf 이하 → 규칙 우선
CONFIDENCE_RULE_TRUSTED = _env_float("CONFIDENCE_RULE_TRUSTED", 0.60)

# ── 개별 필드 신뢰도 (AI/Rule 추출) ────────────────────────────────────────
# field_confidence가 이 값 이상이면 ValidationStatus.OK, 아니면 WARNING
FIELD_CONFIDENCE_OK = _env_float("FIELD_CONFIDENCE_OK", 0.75)

# ── 규칙 분류기 (extractor.classify_text) ──────────────────────────────────
# best_score / 이 값 = coverage (0~1 포화). 결정적 키워드 1개의 weight와 동일.
# 가중치 체계와 깊게 결합되어 있어 env 노출하지 않음.
CLASSIFIER_SATURATION_SCORE = 5.0
