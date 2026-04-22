---
description: PR/릴리스 전 종합 검증 (/test → /lint → /preflight 순서로 실행하고 합격 여부 판정)
---

이 프로젝트의 릴리스 전 루틴을 고정된 순서로 실행하고 최종 판정만 한 블록으로 요약해줘.

## 실행 순서

1. `C:/msys64/mingw64/bin/python.exe -m pytest tests/ -q` — 전체 테스트
2. `C:/Project/데모/doc-auto-mvp/.venv-ai/Scripts/ruff.exe check app/` — 린트
3. `C:/msys64/mingw64/bin/python.exe scripts/preflight_check.py` — 프리플라이트

각 단계는 이전이 실패해도 계속 실행해서 전체 상태를 한 번에 파악할 수 있게 할 것.

## 합격 기준

- pytest: `148 passed, 6 skipped` 이상 (신규 테스트 추가 시 해당 수만큼 증가해야 함)
- ruff: `All checks passed!`
- preflight: 모든 체크 OK

## 출력 형식

```
[release-check]
- pytest:    148 passed, 6 skipped  ✓ / ✗
- ruff:      clean                  ✓ / ✗
- preflight: OK                     ✓ / ✗
→ 판정: 릴리스 준비 완료 / 보류 (사유)
```

## 실패 시 해석 가이드

**ruff 실패** — `pyproject.toml`의 `[tool.ruff] line-length / ignore` 설정 재확인. `tests/`의 기존 F401/I001은 무시(이 명령은 `app/`만 체크).

**pytest 일부 실패** — 단독은 통과하는데 전체에서 실패하면 `tests/conftest.py`의 `_isolate_env`가 `reload_config()`를 시작+종료 두 번 호출하는지 확인(설정 캐시 누수 패턴).

**preflight 실패** — `samples/` 부재: `python scripts/generate_sample_pdfs.py`. 템플릿 부재: `python scripts/create_customer_excel_template.py`.
