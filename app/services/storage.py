from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from app.models import DocumentRecord, DocumentSummary, EditHistoryEntry, LogEntry
from app.schema import build_empty_fields

logger = logging.getLogger(__name__)

# 처리 로그 레벨 규약. append_log의 level 인자는 반드시 이 중 하나여야 함.
#
# - "info":    정상 흐름. 단계 완료, 자동 승인, 분류 결과 등.
#              예) "Field extraction completed", "Auto-approved (confidence: 0.97)"
# - "warning": 부분 실패 또는 대체 경로 사용. 결과는 나왔지만 주의 필요.
#              예) "Vision OCR failed, trying Tesseract", "AI confidence too low",
#                  "Classification mismatch resolved"
# - "error":   완전 실패. 상태가 FAILED가 되거나 필수 필드를 얻지 못함.
#              예) "OCR unavailable", "Extraction failed: ..."
LogLevel = Literal["info", "warning", "error"]

# document_id는 new_document_id()에서 uuid4().hex[:12] — 12자 hex.
# 경로 조합에 쓰이기 전 이 정규식으로 엄격 검증 (traversal / 슬래시 / 공백 방지).
_DOC_ID_RE = re.compile(r"^[a-f0-9]{12}$")


class InvalidDocumentIdError(ValueError):
    """document_id 포맷 위반. endpoint에서 404로 매핑."""

_INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    document_id TEXT PRIMARY KEY,
    uploaded_at REAL NOT NULL,
    status TEXT NOT NULL,
    uploaded_by TEXT,
    original_file_name TEXT,
    warning_count INTEGER DEFAULT 0,
    searchable_text TEXT,
    original_extension TEXT DEFAULT '.pdf',
    requires_ocr INTEGER DEFAULT 0,
    export_file_name TEXT,
    item_count INTEGER DEFAULT 0,
    last_error TEXT,
    auto_approved INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_uploaded_at ON records(uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_status ON records(status);
"""

# 기존 index.db에 컬럼을 뒤늦게 추가할 때 사용 (ALTER TABLE ADD COLUMN).
_SUMMARY_COLUMNS: dict[str, str] = {
    "original_extension": "TEXT DEFAULT '.pdf'",
    "requires_ocr": "INTEGER DEFAULT 0",
    "export_file_name": "TEXT",
    "item_count": "INTEGER DEFAULT 0",
    "last_error": "TEXT",
    "auto_approved": "INTEGER DEFAULT 0",
}

_SORT_SQL = {
    "uploaded_at_asc": "uploaded_at ASC",
    "uploaded_at_desc": "uploaded_at DESC",
    "warning_desc": "warning_count DESC, uploaded_at DESC",
    "failed_first": "(CASE WHEN status='Failed' THEN 0 ELSE 1 END) ASC, warning_count DESC, uploaded_at DESC",
}


class StorageService:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.originals_dir = base_dir / "originals"
        self.ocr_dir = base_dir / "ocr"
        self.raw_json_dir = base_dir / "json" / "raw"
        self.final_json_dir = base_dir / "json" / "final"
        self.exports_dir = base_dir / "exports"
        self.logs_dir = base_dir / "logs"
        self.audit_dir = base_dir / "audit"
        self.records_dir = base_dir / "records"
        for directory in [
            self.originals_dir,
            self.ocr_dir,
            self.raw_json_dir,
            self.final_json_dir,
            self.exports_dir,
            self.logs_dir,
            self.audit_dir,
            self.records_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)
        self._index_db = base_dir / "index.db"
        self._index_lock = threading.Lock()
        self._init_index()

    def _init_index(self) -> None:
        with self._connect() as conn:
            conn.executescript(_INDEX_SCHEMA)
            # 기존 DB에 없는 summary 컬럼 보강 (upgrade path). 이미 있으면 ALTER TABLE이 에러 → catch.
            existing = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
            for col, col_def in _SUMMARY_COLUMNS.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE records ADD COLUMN {col} {col_def}")

    @contextmanager
    def _connect(self):
        with self._index_lock:
            conn = sqlite3.connect(str(self._index_db), timeout=5.0)
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _searchable_text(record: DocumentRecord) -> str:
        parts = [record.document_id, record.original_file_name, record.uploaded_by]
        parts.extend(f.value for f in record.fields if f.value)
        return " ".join(parts).lower()

    _UPSERT_SQL = (
        "INSERT OR REPLACE INTO records "
        "(document_id, uploaded_at, status, uploaded_by, original_file_name, warning_count, searchable_text, "
        " original_extension, requires_ocr, export_file_name, item_count, last_error, auto_approved) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )

    def _index_row(self, record: DocumentRecord) -> tuple:
        uploaded_at = record.uploaded_at
        if uploaded_at.tzinfo is None:
            uploaded_at = uploaded_at.replace(tzinfo=UTC)
        return (
            record.document_id,
            uploaded_at.timestamp(),
            record.status.value,
            record.uploaded_by,
            record.original_file_name,
            len(record.warnings),
            self._searchable_text(record),
            record.original_extension,
            1 if record.requires_ocr else 0,
            record.export_file_name,
            len(record.items),
            record.last_error,
            1 if record.auto_approved else 0,
        )

    def _upsert_index(self, record: DocumentRecord) -> None:
        with self._connect() as conn:
            conn.execute(self._UPSERT_SQL, self._index_row(record))

    def _upsert_index_many(self, records: list[DocumentRecord]) -> None:
        if not records:
            return
        rows = [self._index_row(r) for r in records]
        with self._connect() as conn:
            conn.executemany(self._UPSERT_SQL, rows)

    def rebuild_index(self) -> int:
        """Re-scan all JSON records and rebuild the SQLite index. Returns row count.

        단일 트랜잭션으로 batch upsert — 1만 건 규모에서 per-record commit 대비 수십 배 빠름.
        """
        records: list[DocumentRecord] = []
        for path in self.records_dir.glob("*.json"):
            try:
                records.append(DocumentRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, ValidationError) as exc:
                logger.warning("Skipping corrupt record %s: %s", path.name, exc)
        with self._connect() as conn:
            conn.execute("DELETE FROM records")
            if records:
                conn.executemany(self._UPSERT_SQL, [self._index_row(r) for r in records])
        return len(records)

    def new_document_id(self) -> str:
        return uuid4().hex[:12]

    @staticmethod
    def _validate_id(document_id: str) -> str:
        """Path traversal 방어. new_document_id()는 uuid4().hex[:12] → 12자 hex만 허용."""
        if not _DOC_ID_RE.match(document_id):
            raise InvalidDocumentIdError(f"Invalid document_id: {document_id!r}")
        return document_id

    def record_path(self, document_id: str) -> Path:
        return self.records_dir / f"{self._validate_id(document_id)}.json"

    def original_path(self, document_id: str, extension: str = ".pdf") -> Path:
        normalized = extension if extension.startswith(".") else f".{extension}"
        return self.originals_dir / f"{self._validate_id(document_id)}{normalized.lower()}"

    def ocr_path(self, document_id: str) -> Path:
        return self.ocr_dir / f"{self._validate_id(document_id)}.txt"

    def raw_json_path(self, document_id: str) -> Path:
        return self.raw_json_dir / f"{self._validate_id(document_id)}.json"

    def final_json_path(self, document_id: str) -> Path:
        return self.final_json_dir / f"{self._validate_id(document_id)}.json"

    def export_path(self, document_id: str) -> Path:
        return self.exports_dir / f"{self._validate_id(document_id)}.xlsx"

    def logs_path(self, document_id: str) -> Path:
        return self.logs_dir / f"{self._validate_id(document_id)}.json"

    def audit_path(self, document_id: str) -> Path:
        return self.audit_dir / f"{self._validate_id(document_id)}.json"

    def create_record(self, file_name: str, uploaded_by: str, file_size: int) -> DocumentRecord:
        extension = Path(file_name).suffix.lower() or ".pdf"
        record = DocumentRecord(
            document_id=self.new_document_id(),
            original_file_name=file_name,
            original_extension=extension,
            uploaded_by=uploaded_by,
            uploaded_at=datetime.now(UTC),
            file_size=file_size,
            fields=build_empty_fields(),
        )
        self.save_record(record)
        self.save_logs(record.document_id, [LogEntry(timestamp=datetime.now(UTC), level="info", message="Document uploaded")])
        self.save_audit(record.document_id, [])
        return record

    def save_record(self, record: DocumentRecord) -> None:
        self.record_path(record.document_id).write_text(record.model_dump_json(indent=2), encoding="utf-8")
        try:
            self._upsert_index(record)
        except sqlite3.Error as exc:
            logger.warning(
                "Index upsert failed for %s (JSON saved): %s. Run scripts/rebuild_index.py to re-sync.",
                record.document_id, exc,
            )

    def save_records(self, records: list[DocumentRecord]) -> None:
        """여러 레코드를 한 번에 저장 — 인덱스 upsert가 단일 트랜잭션.

        JSON은 파일당 별도 write(피할 수 없음). SQLite commit은 N → 1로 감소해
        N이 클수록 per-record commit 대비 속도 이득. 부분 실패 시 JSON이 source of truth이므로
        rebuild_index.py로 복구.
        """
        if not records:
            return
        for record in records:
            self.record_path(record.document_id).write_text(record.model_dump_json(indent=2), encoding="utf-8")
        try:
            self._upsert_index_many(records)
        except sqlite3.Error as exc:
            logger.warning(
                "Batch index upsert failed (JSON saved): %s. Run scripts/rebuild_index.py to re-sync.", exc,
            )

    def load_record(self, document_id: str) -> DocumentRecord:
        path = self.record_path(document_id)
        if not path.exists():
            raise FileNotFoundError(document_id)
        return DocumentRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def save_raw_payload(self, document_id: str, payload: dict) -> None:
        self.raw_json_path(document_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_final_payload(self, document_id: str, payload: dict) -> None:
        self.final_json_path(document_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_logs(self, document_id: str) -> list[LogEntry]:
        path = self.logs_path(document_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [LogEntry.model_validate(item) for item in data]

    def save_logs(self, document_id: str, logs: list[LogEntry]) -> None:
        self.logs_path(document_id).write_text(
            json.dumps([item.model_dump(mode="json") for item in logs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append_log(self, document_id: str, level: LogLevel, message: str) -> None:
        """로그 항목 추가. level은 LogLevel Literal 중 하나 — 규약은 모듈 상단 참조."""
        logs = self.load_logs(document_id)
        logs.append(LogEntry(timestamp=datetime.now(UTC), level=level, message=message))
        self.save_logs(document_id, logs)

    def load_audit(self, document_id: str) -> list[EditHistoryEntry]:
        path = self.audit_path(document_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [EditHistoryEntry.model_validate(item) for item in data]

    def save_audit(self, document_id: str, entries: list[EditHistoryEntry]) -> None:
        self.audit_path(document_id).write_text(
            json.dumps([item.model_dump(mode="json") for item in entries], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append_audit(self, document_id: str, entry: EditHistoryEntry) -> None:
        entries = self.load_audit(document_id)
        entries.append(entry)
        self.save_audit(document_id, entries)

    def count_records(self, query: str = "", status: str = "") -> int:
        """Count records matching optional filters (via SQLite index)."""
        where, params = self._index_filter(query, status)
        sql = "SELECT COUNT(*) FROM records" + (f" WHERE {where}" if where else "")
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def dashboard_counts(self) -> dict[str, int]:
        """대시보드 집계를 단일 쿼리로 — status별 카운트 + auto_approved 합계.

        반환: {"total", "Failed", "Needs Review", "Exported", "auto_approved"}.
        status 키에 없는 상태는 0으로 채워 호출자가 KeyError 걱정 없이 씀.
        """
        with self._connect() as conn:
            status_rows = conn.execute(
                "SELECT status, COUNT(*), SUM(auto_approved) FROM records GROUP BY status"
            ).fetchall()
        totals: dict[str, int] = {
            "total": 0, "Failed": 0, "Needs Review": 0, "Exported": 0, "auto_approved": 0,
        }
        for status, count, approved_sum in status_rows:
            totals["total"] += int(count or 0)
            totals["auto_approved"] += int(approved_sum or 0)
            if status in totals:
                totals[status] = int(count or 0)
        return totals

    @staticmethod
    def _index_filter(query: str, status: str) -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []
        if query:
            clauses.append("searchable_text LIKE ?")
            params.append(f"%{query.lower()}%")
        if status:
            clauses.append("LOWER(status) = ?")
            params.append(status.lower())
        return " AND ".join(clauses), params

    def list_records(
        self, query: str = "", status: str = "", sort: str = "uploaded_at_desc",
        limit: int = 100, offset: int = 0,
    ) -> tuple[list[DocumentRecord], int]:
        """Return (paginated_records, total_filtered_count) via SQLite index.

        각 ID마다 JSON을 전체 로드하므로 비용 큼 — 목록 표시에는 `list_summaries`를 선호.
        """
        where, params = self._index_filter(query, status)
        order_by = _SORT_SQL.get(sort, _SORT_SQL["uploaded_at_desc"])
        base_sql = "FROM records" + (f" WHERE {where}" if where else "")

        with self._connect() as conn:
            total_row = conn.execute(f"SELECT COUNT(*) {base_sql}", params).fetchone()
            total = int(total_row[0]) if total_row else 0
            rows = conn.execute(
                f"SELECT document_id {base_sql} ORDER BY {order_by} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()

        records: list[DocumentRecord] = []
        for (document_id,) in rows:
            try:
                records.append(self.load_record(document_id))
            except (FileNotFoundError, json.JSONDecodeError, ValidationError) as exc:
                logger.warning("Skipping missing/corrupt record %s: %s", document_id, exc)
        return records, total

    def list_summaries(
        self, query: str = "", status: str = "", sort: str = "uploaded_at_desc",
        limit: int = 100, offset: int = 0,
    ) -> tuple[list[DocumentSummary], int]:
        """인덱스 DB에서 DocumentSummary를 직접 만들어 반환. JSON 파일 로드 없음.

        대규모 스케일(10k+ 건)에서 목록/대시보드 응답 시간을 크게 단축함.
        최신 필드(last_error, requires_ocr 등)는 save_record 시점에 인덱스에 동기화됨.
        """
        where, params = self._index_filter(query, status)
        order_by = _SORT_SQL.get(sort, _SORT_SQL["uploaded_at_desc"])
        base_sql = "FROM records" + (f" WHERE {where}" if where else "")

        with self._connect() as conn:
            total_row = conn.execute(f"SELECT COUNT(*) {base_sql}", params).fetchone()
            total = int(total_row[0]) if total_row else 0
            rows = conn.execute(
                f"SELECT document_id, uploaded_at, status, uploaded_by, original_file_name, "
                f"       warning_count, original_extension, requires_ocr, export_file_name, "
                f"       item_count, last_error "
                f"{base_sql} ORDER BY {order_by} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()

        summaries = [
            DocumentSummary(
                document_id=r[0],
                uploaded_at=datetime.fromtimestamp(r[1], tz=UTC),
                status=r[2],  # type: ignore[arg-type]  # Pydantic coerces str → StrEnum
                uploaded_by=r[3] or "",
                original_file_name=r[4] or "",
                warning_count=int(r[5] or 0),
                original_extension=r[6] or ".pdf",
                requires_ocr=bool(r[7]),
                export_file_name=r[8],
                item_count=int(r[9] or 0),
                last_error=r[10],
            )
            for r in rows
        ]
        return summaries, total

    def _sort_stamp(self, record: DocumentRecord) -> float:
        uploaded_at = record.uploaded_at
        if uploaded_at.tzinfo is None:
            uploaded_at = uploaded_at.replace(tzinfo=UTC)
        return uploaded_at.timestamp()
