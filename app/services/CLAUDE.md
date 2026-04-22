# app/services/ — 서비스 레이어 심화 규칙

이 파일은 `app/services/*.py` 편집 시에만 자동 로드. 루트 `CLAUDE.md` + `app/CLAUDE.md`의 원칙 위에 덧붙임.

## 문서 유형 분류기 3단계

### 1) 규칙 분류기 — `extractor.classify_text(text) → (type, confidence)`

- 유형별 가중치 시그널 테이블: `CLASSIFIER_SIGNALS`
- 시그널 종류: `"kw"`(키워드) / `"re"`(정규식) / `"neg"`(음의 증거, 점수 차감)
- 가중치 규약: `5.0` = 결정적 키워드 1개, `2~3` = 중간, `1` = 약한 증거
- 매칭 규칙:
  - **ASCII 키워드**는 반드시 `\b` 경계로 보호해 substring 오매칭 방지 (예: `contract` ⊄ `contractor`)
  - **한글 키워드**는 `_strip_spaces()`로 compact 매칭 → "전 자 세 금 계 산 서" ≡ "전자세금계산서"
  - **정규식**은 `normalized = " ".join(text.split())`(개행 → 공백)에 매칭 → OCR 라인 분리 강건
- Confidence 공식 (임계값 상수는 `app/confidence_thresholds.py`):
  - `coverage = min(best_score / CLASSIFIER_SATURATION_SCORE, 1.0)` (기본 5.0, 결정적 키워드 1개만 있어도 포화)
  - `decisiveness = (best - runner_up) / best`
  - `confidence = coverage * (0.5 + 0.5 * decisiveness)` (상한 0.99)
  - 이전의 `score / max_possibles` 공식은 max가 너무 커 저평가됐음 → 교체됨

### 2) AI 분류기 — `ai_structurer.classify_and_extract`

- OpenAI Responses API + JSON Schema로 분류+추출 **1회** 호출
- 프롬프트(`DOCUMENT_TYPE_PROMPT`)에 tie-breaker 5건:
  1. 카드매출전표/승인번호 ∧ ¬세금계산서 → 영수증
  2. 세금계산서 ∧ 공급자+공급받는자 둘 다 → 전자세금계산서
  3. 거래명세서 헤더 우세 → 거래명세서
  4. 계약서 + 계약기간/계약금액 → 외부용역계약서
  5. 견적서 + 개발용역/SI → 개발용역견적서, 아니면 일반견적서
- 빈 결과로 반환해야 할 때는 confidence 0.0 강제

### 3) 병합 — `workflow._merge_classifications`

우선순위 (first match wins):
1. `forced_type` 지정 → 항상 승
2. AI ∧ 규칙 일치 → 그 유형
3. 단일 결과 존재 → 그 유형
4. 불일치 (임계값은 `CONFIDENCE_AI_TRUSTED` / `CONFIDENCE_AI_MARGIN_OVER_RULE` / `CONFIDENCE_RULE_TRUSTED` 환경변수로 오버라이드):
   - AI conf ≥ 0.85 ∧ AI conf ≥ 규칙 conf + 0.10 → AI 승
   - 규칙 conf ≥ 0.60 ∧ 규칙 conf ≥ AI conf → 규칙 승
   - 둘 다 저신뢰 → 높은 쪽 (tie는 AI) + warning
5. 둘 다 공란 → 기본값 `전자세금계산서` + warning

## AI 응답 캐시 — `ai_cache.py`

- 키: `sha256(namespace + prompt_fingerprint + model + full_text)` — prompt 문자열 자체가 지문이므로 프롬프트 수정 시 자동 cache miss
- 저장: `storage/ai_cache/*.json`, atomic rename(tmp → final)으로 동시 쓰기 안전
- 방출: LRU, 기본 `AI_CACHE_MAX_ENTRIES=2000`. `set()`이 `_write_lock` 안에서 쓰기+프루닝 수행해 막 쓴 파일이 방출되지 않음(`protected=path`)
- `classify_and_extract`는 `text[:6000]`를 API로 보내지만 **키는 full text**로 생성 — 상단 boilerplate가 같은 다른 문서끼리 키 충돌 방지

## 엑셀 내보내기 — `exporter.py` ↔ `template_mapping.py`

**동기 의무**: 다음 셋은 항상 묶여 움직인다.
- `exporter.py` — 실제 쓰기 로직
- `template_mapping.py` — 시트 이름 상수(`SUMMARY_SHEET_NAME`, `ITEM_SHEET_NAME`, `AUDIT_SHEET_NAME`), 셀 좌표 맵
- `tests/test_exporter*.py` 4개 파일 — 서식/수식/감사 시트 검증

레이아웃 변경 시 네 곳 전부 수정. 한 곳만 바꾸면 silent failure (시트 이름 mismatch → 빈 시트).

**주요 규칙**:
- 금액/날짜는 Excel 네이티브 타입으로 (`_to_number`, `_to_date` 헬퍼)
- 품목 행 수는 동적 (`max(38, len(items) + 5)`) — 38행 하드코딩 제거됨
- 합계 행은 수식 (`=SUM(E{start}:E{last})`)
- 한글 폰트 `Font(name="맑은 고딕", size=10)` 전체 적용
- 빈 필드는 `_mark_empty`로 노란 fill + "(미입력)" 이탤릭
- 감사 시트: 필드 → 편집 이력 → 처리 로그 3블록

## Vision OCR 병렬화 — `vision_ocr.py`

- 페이지 병렬 처리: `asyncio.gather(*tasks, return_exceptions=False)` + `asyncio.Semaphore(VISION_CONCURRENCY)` (기본 4). 예외는 전파해 workflow에서 `VisionOcrUnavailableError` 처리.
- 동기 진입점은 루프 감지 후 `asyncio.run` 또는 `ThreadPoolExecutor` fallback
- 이미지 전처리: `VISION_IMAGE_MAX_DIM=512`로 다운스케일 + JPEG 품질 70 → 토큰/대역 절감
- `chat.completions.create` + `{"url": ..., "detail": "low"}` 형식 사용 (`responses.create` 아님)

## SQLite 인덱스 — `storage.py`

- 경로: `storage/index.db`, 테이블 `records(document_id PK, uploaded_at, status, uploaded_by, original_file_name, warning_count, searchable_text)`
- `save_record`는 JSON 쓰기 + SQL upsert를 같이 수행 (JSON이 source of truth, 인덱스는 조회 가속)
- `list_records` / `count_records`는 SQL 경로만 사용 — 1만 건 규모에서 <1s. 단 `list_records`는 여전히 건당 JSON을 로드하므로 **목록/집계에는 `list_summaries` + `dashboard_counts`를 선호**
- `list_summaries`는 JSON 로드 없이 인덱스 row에서 `DocumentSummary`를 직접 생성 — `/documents` 목록 엔드포인트가 사용. 인덱스 스키마에 `last_error`, `requires_ocr`, `export_file_name`, `item_count`, `original_extension`, `auto_approved` 컬럼 포함 (기존 DB는 `_init_index`에서 ALTER TABLE로 자동 보강)
- `dashboard_counts()`는 `status` × `auto_approved`를 단일 `GROUP BY` 쿼리로 집계 — `/documents/dashboard` 전용. 기존 `list_records(limit=500)` 기반 구현은 500건만 샘플링돼 totals가 축소되는 버그가 있었음
- **기존 `index.db` 마이그레이션 주의**: `auto_approved` 컬럼은 ALTER TABLE 보강 시 기본 0 — 이전 레코드의 auto_approved 카운트가 0으로 보이면 `python scripts/rebuild_index.py` 실행
- `_connect` contextmanager는 `threading.Lock` + `timeout=5.0`으로 동시성 보호
- 인덱스 손상/누락 시 `python scripts/rebuild_index.py`로 재생성

## 공통 워크플로우 — `workflow.py`

- 신뢰도 라우팅(`_apply_confidence_routing`): `avg_confidence` + `missing_required` + `_amounts_cross_check` 3요소. 기준 0.95 / 0.80.
- `_reshape_fields_to_schema`: 수동 재분류 시 목표 유형 스키마로 필드 재구성. name 매칭으로 값 유지, 타깃 스키마에 없는 필드는 제거.
- `extract`에 `forced_type` 인자 → 재분류 엔드포인트에서 주입.
