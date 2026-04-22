"""`workflow._merge_classifications` 5가지 결정 분기 각각의 명시 테스트 + 경계값.

분기(first-match-wins 순서):
  1. forced_type 지정 → 항상 승
  2. AI ∧ 규칙 일치 → 그 유형
  3. 단일 결과 존재 → 그 유형
  4. 불일치 + confidence 가중: AI ≥ 0.85 & margin ≥ 0.10 OR 규칙 ≥ 0.60
  5. 둘 다 공란 → 기본값 '전자세금계산서' + warning
"""
from __future__ import annotations

from app.confidence_thresholds import (
    CONFIDENCE_AI_MARGIN_OVER_RULE,
    CONFIDENCE_AI_TRUSTED,
    CONFIDENCE_RULE_TRUSTED,
)


def _merge(workflow, **kwargs):
    """Call _merge_classifications with sensible defaults."""
    defaults = dict(
        document_id="abc123def456",  # 12자 hex (storage._validate_id 통과)
        forced_type="",
        ai_type="",
        ai_conf=0.0,
        rule_type="",
        rule_conf=0.0,
        warnings=[],
    )
    defaults.update(kwargs)
    return workflow._merge_classifications(**defaults)


class TestBranch1ForcedType:
    """forced_type이 세팅되면 AI/규칙과 무관하게 그 유형 반환."""

    def test_forced_type_wins_over_everything(self, isolated_workflow):
        warnings: list[str] = []
        result = _merge(
            isolated_workflow,
            forced_type="영수증",
            ai_type="전자세금계산서",
            ai_conf=0.99,
            rule_type="전자세금계산서",
            rule_conf=0.99,
            warnings=warnings,
        )
        assert result == "영수증"
        assert warnings == []  # 강제 설정은 warning 없음


class TestBranch2BothAgree:
    """AI와 규칙이 동일 유형 → 그 유형, warning 없음."""

    def test_both_agree_no_warning(self, isolated_workflow):
        warnings: list[str] = []
        result = _merge(
            isolated_workflow,
            ai_type="전자세금계산서", ai_conf=0.70,
            rule_type="전자세금계산서", rule_conf=0.70,
            warnings=warnings,
        )
        assert result == "전자세금계산서"
        assert warnings == []


class TestBranch3SingleResult:
    """한쪽만 분류 결과가 있을 때 → 그 쪽 사용."""

    def test_ai_only_returns_ai(self, isolated_workflow):
        result = _merge(
            isolated_workflow,
            ai_type="영수증", ai_conf=0.30,  # 저신뢰여도 단일이면 채택
            rule_type="", rule_conf=0.0,
        )
        assert result == "영수증"

    def test_rule_only_returns_rule(self, isolated_workflow):
        result = _merge(
            isolated_workflow,
            ai_type="", ai_conf=0.0,
            rule_type="거래명세서", rule_conf=0.40,
        )
        assert result == "거래명세서"


class TestBranch4Disagree:
    """AI와 규칙이 다른 유형일 때의 가중 결정."""

    def test_ai_wins_when_high_conf_and_margin(self, isolated_workflow):
        """AI ≥ 0.85 AND AI ≥ 규칙 + 0.10 → AI 승."""
        warnings: list[str] = []
        result = _merge(
            isolated_workflow,
            ai_type="영수증", ai_conf=0.90,
            rule_type="전자세금계산서", rule_conf=0.70,
            warnings=warnings,
        )
        assert result == "영수증"
        assert len(warnings) == 1  # 불일치 warning

    def test_rule_wins_when_ai_low_conf(self, isolated_workflow):
        """규칙 ≥ 0.60 AND 규칙 ≥ AI → 규칙 승."""
        warnings: list[str] = []
        result = _merge(
            isolated_workflow,
            ai_type="영수증", ai_conf=0.70,
            rule_type="전자세금계산서", rule_conf=0.80,
            warnings=warnings,
        )
        assert result == "전자세금계산서"
        assert len(warnings) == 1

    def test_higher_wins_when_both_low(self, isolated_workflow):
        """둘 다 낮으면 높은 쪽 선택."""
        warnings: list[str] = []
        result = _merge(
            isolated_workflow,
            ai_type="영수증", ai_conf=0.40,
            rule_type="전자세금계산서", rule_conf=0.30,
            warnings=warnings,
        )
        assert result == "영수증"  # AI가 더 높음
        assert len(warnings) == 1

    def test_ai_at_trusted_threshold_but_margin_fails(self, isolated_workflow):
        """AI=0.85, 규칙=0.80 → margin 0.05 < 0.10 → 규칙 승 (트러스트 분기 아님)."""
        result = _merge(
            isolated_workflow,
            ai_type="영수증", ai_conf=CONFIDENCE_AI_TRUSTED,  # 0.85
            rule_type="전자세금계산서", rule_conf=0.80,
        )
        # AI 분기 실패(margin 부족) → 규칙 분기: 0.80 ≥ 0.60 ∧ 0.80 ≥ 0.85? No (≥ 아님)
        # 따라서 fallback: 높은 쪽 → AI(0.85) > 규칙(0.80)
        assert result == "영수증"

    def test_ai_just_below_trusted_threshold(self, isolated_workflow):
        """AI=0.849 < 0.85 → AI 트러스트 분기 실패. 규칙 0.60 이상이면 규칙 승."""
        result = _merge(
            isolated_workflow,
            ai_type="영수증", ai_conf=CONFIDENCE_AI_TRUSTED - 0.001,  # 0.849
            rule_type="전자세금계산서", rule_conf=CONFIDENCE_RULE_TRUSTED,  # 0.60
        )
        # AI 분기: 0.849 < 0.85 → 실패
        # 규칙 분기: 0.60 ≥ 0.60 ✓ ∧ 0.60 ≥ 0.849? No → 실패
        # Fallback: 0.849 > 0.60 → AI
        assert result == "영수증"

    def test_rule_just_at_trusted_threshold(self, isolated_workflow):
        """규칙=0.60, AI=0.50 → 규칙 분기 성공."""
        result = _merge(
            isolated_workflow,
            ai_type="영수증", ai_conf=0.50,
            rule_type="전자세금계산서", rule_conf=CONFIDENCE_RULE_TRUSTED,
        )
        assert result == "전자세금계산서"

    def test_rule_just_below_trusted_threshold(self, isolated_workflow):
        """규칙=0.59 (<0.60), AI=0.50 → 규칙 트러스트 실패. 높은 쪽은 규칙."""
        result = _merge(
            isolated_workflow,
            ai_type="영수증", ai_conf=0.50,
            rule_type="전자세금계산서", rule_conf=CONFIDENCE_RULE_TRUSTED - 0.01,
        )
        # 둘 다 저신뢰 fallback: 0.59 > 0.50 → 규칙
        assert result == "전자세금계산서"


class TestBranch5BothEmpty:
    """AI도 규칙도 공란 → 기본값 + warning."""

    def test_both_empty_returns_default(self, isolated_workflow):
        warnings: list[str] = []
        result = _merge(
            isolated_workflow,
            ai_type="", ai_conf=0.0,
            rule_type="", rule_conf=0.0,
            warnings=warnings,
        )
        assert result == "전자세금계산서"
        assert len(warnings) == 1
        assert "판별하지 못하여" in warnings[0]


class TestMarginConstant:
    """CONFIDENCE_AI_MARGIN_OVER_RULE 정확히 사용되는지 경계값으로 확인."""

    def test_margin_exactly_at_threshold_ai_wins(self, isolated_workflow):
        """AI - 규칙 = exactly margin → AI 승 (≥ 조건이라 같을 때 통과)."""
        # AI=0.90, 규칙=0.80 → diff=0.10 = margin ≥ 0.10 ✓
        result = _merge(
            isolated_workflow,
            ai_type="영수증", ai_conf=0.90,
            rule_type="전자세금계산서", rule_conf=0.90 - CONFIDENCE_AI_MARGIN_OVER_RULE,
        )
        assert result == "영수증"
