from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# 기존 단일 파일(마이그레이션 전 레거시). 신규 저장은 corrections_YYYY-MM-DD.json으로 진행.
_LEGACY_FILE = "corrections.json"


class FeedbackEntry(BaseModel):
    timestamp: datetime
    document_id: str
    document_type: str
    field_name: str
    ai_value: str
    corrected_value: str
    extraction_source: str


class FeedbackCollector:
    """Collects AI extraction corrections for accuracy tracking and prompt improvement.

    저장 전략 (2026-04 변경): 기존 `corrections.json` 단일 파일 → 일 단위 분리
    (`corrections_YYYY-MM-DD.json`). 기존 파일이 있으면 읽기 전용으로 함께 집계.
    매 수정마다 전체 파일을 읽고 쓰던 패턴이 제거돼 누적 부담 대폭 감소.
    """

    def __init__(self, base_dir: Path) -> None:
        self.feedback_dir = base_dir / "feedback"
        self.feedback_dir.mkdir(parents=True, exist_ok=True)

    def collect_correction(
        self,
        document_id: str,
        document_type: str,
        field_name: str,
        ai_value: str,
        corrected_value: str,
        extraction_source: str,
    ) -> None:
        """Record a field correction (old AI/rule value vs human-corrected value).

        오늘자 파일에만 read/append/write. 과거 누적 파일은 건드리지 않아 파일 크기
        일 단위로 캡됨.
        """
        if ai_value == corrected_value:
            return

        entry = FeedbackEntry(
            timestamp=datetime.now(UTC),
            document_id=document_id,
            document_type=document_type,
            field_name=field_name,
            ai_value=ai_value,
            corrected_value=corrected_value,
            extraction_source=extraction_source,
        )

        try:
            today_path = self._day_path(entry.timestamp)
            entries = self._load_file(today_path)
            entries.append(entry)
            self._save_file(today_path, entries)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to save feedback for %s/%s: %s", document_id, field_name, exc)

    def get_accuracy_stats(self, document_type: str = "") -> dict[str, object]:
        """Get field-level accuracy statistics, optionally filtered by document type."""
        entries = self._load_all_entries()
        if document_type:
            entries = [e for e in entries if e.document_type == document_type]

        if not entries:
            return {"total_corrections": 0, "fields": {}}

        field_stats: dict[str, dict[str, int]] = {}
        for entry in entries:
            if entry.field_name not in field_stats:
                field_stats[entry.field_name] = {"corrections": 0, "ai_corrections": 0, "rule_corrections": 0}
            field_stats[entry.field_name]["corrections"] += 1
            if entry.extraction_source == "ai":
                field_stats[entry.field_name]["ai_corrections"] += 1
            else:
                field_stats[entry.field_name]["rule_corrections"] += 1

        return {
            "total_corrections": len(entries),
            "document_type_filter": document_type or "all",
            "fields": field_stats,
        }

    def get_recent_corrections(self, field_name: str, limit: int = 5) -> list[FeedbackEntry]:
        """Get recent corrections for a specific field (useful for few-shot prompt injection)."""
        entries = self._load_all_entries()
        relevant = [e for e in entries if e.field_name == field_name]
        return sorted(relevant, key=lambda e: e.timestamp, reverse=True)[:limit]

    def _day_path(self, ts: datetime) -> Path:
        """오늘자 피드백 파일 경로. UTC 기준 날짜로 롤오버."""
        return self.feedback_dir / f"corrections_{ts.strftime('%Y-%m-%d')}.json"

    def _load_all_entries(self) -> list[FeedbackEntry]:
        """레거시 단일 파일 + 모든 날짜별 파일을 합쳐 반환."""
        entries: list[FeedbackEntry] = []
        legacy = self.feedback_dir / _LEGACY_FILE
        if legacy.exists():
            entries.extend(self._load_file(legacy))
        for path in sorted(self.feedback_dir.glob("corrections_*.json")):
            entries.extend(self._load_file(path))
        return entries

    def _load_file(self, path: Path) -> list[FeedbackEntry]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [FeedbackEntry.model_validate(item) for item in data]
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            logger.warning("Failed to load feedback file %s: %s", path.name, exc)
            return []

    def _save_file(self, path: Path, entries: list[FeedbackEntry]) -> None:
        data = json.dumps([e.model_dump(mode="json") for e in entries], ensure_ascii=False, indent=2)
        # Atomic write via temp file to prevent partial writes on concurrent access
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(data, encoding="utf-8")
        tmp_path.replace(path)
