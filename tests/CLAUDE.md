# tests/ — 테스트 작업 가이드

이 파일은 `tests/` 내 파일을 편집할 때 자동 로드됨. 루트 `CLAUDE.md`의 기술 스택·코드 컨벤션과 함께 적용.

## 실행

```bash
# 전체
python -m pytest tests/ -v

# 단일 파일
python -m pytest tests/test_extractor.py -v

# 커버리지
python -m pytest tests/ --cov=app --cov-report=term-missing
```

## 테스트 격리 (`tests/conftest.py`)

**모듈 import 시점**: `os.environ.setdefault("RATE_LIMIT_ENABLED", "0")` 주입 → 반복 호출 시 429 방지.

**`_isolate_env` autouse 픽스처** (매 테스트 전후):
- `monkeypatch.delenv`로 `OPENAI_STRUCTURING_ENABLED`, `OPENAI_API_KEY`, `API_KEY`, `CORS_ORIGINS` 제거
- `limiter.reset()`으로 rate limit 카운터 초기화
- `schema.reload_config()` 시작 + 종료 시 호출 (유형 설정 캐시 리셋)

임시 저장소는 별도의 `isolated_storage` 픽스처가 `tmp_path`로 생성 — `_isolate_env`는 스토리지에 관여하지 않음.

## 주요 픽스처

| 픽스처 | 용도 |
|--------|------|
| `isolated_storage` | `tmp_path` 기반 격리된 `StorageService` |
| `isolated_workflow` | `isolated_storage`를 쓰는 `DocumentWorkflowService`. OCR/PDF 서비스는 실제 인스턴스라서 테스트에서 별도 monkeypatch 필요 |
| `isolated_app` | `app.main.storage` / `app.main.workflow`를 격리 인스턴스로 치환한 FastAPI TestClient |

## 모킹 패턴

**PDF 텍스트 추출**:
```python
from app.services.pdf_text import TextExtractionResult
monkeypatch.setattr(
    isolated_workflow.pdf_text_service, "extract",
    lambda _: TextExtractionResult(text="전자세금계산서...", requires_ocr=False, warnings=[]),
)
```

**OpenAI API**:
- 기본 상태(`OPENAI_STRUCTURING_ENABLED` 미설정)에서는 AI 경로 비활성 → 규칙 기반만 실행
- AI 경로 테스트가 필요하면 `OpenAIStructurer._get_client`나 `classify_and_extract`를 직접 monkeypatch

**파일 업로드** (helper `_upload_pdf_to_storage`):
```python
record = storage.create_record("stub.pdf", "tester", 10)
storage.original_path(record.document_id, ".pdf").write_bytes(b"%PDF-1.4 stub")
```

## 파일별 포커스

- `test_app.py` — FastAPI TestClient, workflow monkeypatch 패턴
- `test_extractor.py` — 순수 텍스트 입력으로 추출 로직 단위 테스트
- `test_classifier.py` — `classify_text` 분류기 (가중치/confidence/혼동 케이스)
- `test_sprint1_features.py` — 재분류 / 일괄검수 / 재내보내기 통합 테스트
- `test_sprint2b_features.py` — list_summaries / 스키마 마이그레이션 / feedback 롤오버
- `test_merge_classifications.py` — `workflow._merge_classifications` 5분기 + 경계값
- `test_security.py` — 매직 바이트 검증 / API_KEY / path traversal / CORS
- `test_storage_index.py` — SQLite 인덱스 성능/동시성
- `test_exporter*.py` — 엑셀 서식·수식·감사 시트
- `test_readiness_endpoint_returns_preflight_status` — `samples/` 디렉토리 상태 의존

## 주의

- `storage/` 디렉토리를 테스트가 공유하면 안 됨 — 반드시 `isolated_storage` 사용.
- `test_settings.TestConfigReload` 같은 설정 캐시를 건드리는 테스트 이후 cache leak 방지 위해 `_isolate_env`가 `reload_config()` 두 번 호출함.
- AI-first 경로를 실제로 타는 테스트를 쓰는 경우 `OPENAI_STRUCTURING_ENABLED=true`를 monkeypatch로 주입하고 `OpenAIStructurer`를 반드시 mocking (실제 API 호출 금지).
