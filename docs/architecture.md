# Architecture

> 인간 열람용 상세 문서. Claude Code 세션에는 **자동 로드되지 않음** — 코드 작업 시에는 `app/CLAUDE.md`(요약) 또는 `app/services/CLAUDE.md`(심화)가 자동 로드된다. 이 파일은 신규 팀원 온보딩·스펙 리뷰·장기 아카이브용.

## 프로젝트 구조

```
app/
  main.py              # FastAPI 엔드포인트
  models.py            # Pydantic 데이터 모델 (DocumentRecord 중심)
  schema.py            # 문서 유형별 필드 프로파일 (config JSON 기반)
  settings.py          # 경로 상수
  template_mapping.py  # 엑셀 시트 레이아웃 매핑 (config JSON 기반)
  services/
    workflow.py        # 비즈니스 로직 오케스트레이션
    pdf_text.py        # pypdf 텍스트 추출
    ocr.py             # Tesseract/Ghostscript OCR (fallback)
    vision_ocr.py      # OpenAI Vision API OCR (우선)
    extractor.py       # 규칙 기반 필드 추출 + 문서 유형 분류기
    ai_structurer.py   # AI 주 추출 엔진 (분류+추출+라인아이템)
    ai_cache.py        # sha256 기반 응답 캐시
    exporter.py        # 엑셀 내보내기
    feedback_collector.py  # 검수 피드백 수집/통계
    storage.py         # JSON 레코드 + SQLite 인덱스
  templates/           # Jinja2 HTML
  static/              # JS/CSS
config/
  document_types.json  # 문서 유형별 스키마/레이아웃 설정
tests/                 # pytest 테스트
scripts/               # 유틸리티 스크립트
storage/               # 런타임 데이터 (records, logs, audit, json, exports, feedback, index.db)
templates/             # 고객 엑셀 템플릿
tools/                 # Tesseract 바이너리/언어 데이터
samples/               # 샘플 PDF 및 테스트 케이스
```

## 문서 처리 파이프라인

```
Upload → [매직 바이트 검증] → PDF텍스트추출 → (Vision OCR / Tesseract) → AI분류+추출 → [AI 캐시] → 규칙기반검증 → 신뢰도라우팅 → 검수 → 엑셀내보내기
```

- **매직 바이트 검증**: 업로드 시 확장자와 실제 파일 시그니처(예: `%PDF-`, `\x89PNG`, `\xFF\xD8\xFF`) 일치 확인 — 확장자 스푸핑 방지.
- **AI 캐시** (`app/services/ai_cache.py`): sha256(namespace + prompt_fingerprint + model + full text) 키로 OpenAI 응답 캐시. 동일 문서 재추출 시 API 호출 0. LRU + atomic write.

## AI-First vs Rule-Based 모드

- `OPENAI_STRUCTURING_ENABLED=true`: AI 분류+추출 (1회 API, 캐시 hit 시 0) → 규칙 기반 교차검증 → AI 실패 시 규칙 fallback
- `OPENAI_STRUCTURING_ENABLED=false`: 규칙 기반 추출만 사용

## 상태 흐름

```
Uploaded → Processing (비동기) → Needs Review / Reviewed (자동승인) / Failed
Needs Review → Reviewed (검수 완료)
Reviewed → Exported (엑셀 생성)
Exported → Exported (재생성 허용)
Failed → Needs Review (재시도 성공)
```

## 신뢰도 라우팅

- confidence >= 0.95 + 필수필드 완전 + 금액검증 통과 → `REVIEWED` (자동 승인)
- confidence >= 0.80 → `NEEDS_REVIEW` (일반 검수)
- confidence < 0.80 → `NEEDS_REVIEW` + 우선 검수 (review_priority=high)

## 문서 유형 분류

- **규칙 분류기** (`extractor.classify_text`): 유형별 가중치 키워드/정규식 시그널 → (type, confidence). ASCII 키워드 `\b` 경계 보호, 한글은 공백 허용 compact 매칭, 정규식은 normalized 텍스트(개행→공백)에 매칭.
- **AI 분류기** (`ai_structurer.classify_and_extract`): OpenAI Responses API + JSON Schema, tie-breaker 5건 프롬프트.
- **병합** (`workflow._merge_classifications`): forced_type > 양쪽 일치 > 단일 결과 > confidence 비교(AI ≥ 0.85 & margin ≥ 0.10 / 규칙 ≥ 0.60) > 기본값(전자세금계산서).
- **수동 재분류** (`POST /documents/{id}/reclassify?document_type=...`): 자동 분류 결과를 사람이 override. `workflow._reshape_fields_to_schema`가 목표 유형 스키마로 필드 재구성하면서 기존 값 name 매칭으로 유지.
- 임계값은 `app/confidence_thresholds.py` 중앙 관리 — env 오버라이드 가능.

## 지원 문서 유형 (`config/document_types.json`에서 관리)

1. 전자세금계산서 (기본)
2. 거래명세서
3. 외부용역계약서
4. 개발용역견적서
5. 일반견적서 (QUOTATION/見積書)
6. 영수증 (카드매출전표/현금영수증/간이영수증) — 전용 필드: 승인번호, 카드번호(마스킹), 거래일시, 봉사료

새 유형 추가: `config/document_types.json`에 1곳만 추가하면 스키마/레이아웃/엑셀 내보내기까지 반영됨.
유형별 전용 필드(예: 영수증의 승인번호)는 `app/schema.py`의 `TYPE_SPECIFIC_FIELDS`에 추가.

## 배치 검수 (`POST /documents/review/batch`)

여러 문서를 한 번에 `REVIEWED`로 승격. 카운트는 3분류:
- `success`: 필수 필드 완비 + 검수 완료
- `missing_fields`: 필수 필드 누락으로 `NEEDS_REVIEW` 유지
- `failed`: 문서 없음 등 예외
- 불변식: `success + missing_fields + failed == total`
