# Doc Auto MVP

세금계산서 PDF를 업로드해 핵심 필드를 추출하고, 검수 후 엑셀로 내보내는 MVP입니다.

## 실행

```powershell
cd C:\Project\데모\doc-auto-mvp
python -m pip install -e .[dev]
python -m uvicorn app.main:app --reload --port 8000
```

브라우저에서 `http://127.0.0.1:8000`으로 접속합니다.

## 주요 기능

1. PDF 업로드
2. 텍스트 PDF 파싱
3. 규칙 기반 필드 추출
4. 신뢰도/검증 상태 표시
5. 검수 후 엑셀 다운로드
6. 처리 로그 및 수정 이력 저장

## 현재 제한

1. 스캔 PDF OCR은 Tesseract 설치가 필요합니다. 설치 후 `TESSERACT_CMD` 환경변수를 지정하거나 기본 설치 경로를 사용합니다.
2. AI 구조화 계층은 확장 포인트를 두고 현재는 규칙 기반 추출기로 기본 동작합니다.

## OCR 설정

Windows 예시:

```powershell
$env:TESSERACT_CMD="C:\Program Files\Tesseract-OCR\tesseract.exe"
python -m uvicorn app.main:app --reload --port 8000
```

로컬 설치 시도:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_tesseract_local.ps1
```

설치 상태 점검:

```powershell
python .\scripts\ocr_smoke_test.py
python .\scripts\preflight_check.py
```

권장 언어 데이터:
1. `kor`
2. `eng`

## OpenAI 구조화 계층 설정

규칙 기반 추출 후 OpenAI로 필드 보정을 추가하려면 아래 환경변수를 설정합니다.

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:OPENAI_STRUCTURING_ENABLED="true"
$env:OPENAI_STRUCTURING_MODEL="gpt-5.4-mini"
```

선택 설치:

```powershell
python -m pip install -e .[ai]
```

동작 방식:
1. 규칙 기반 추출 수행
2. OpenAI Responses API로 필드 보정 시도
3. 응답이 유효하면 필드 값을 보정
4. 실패하면 규칙 기반 결과를 유지
