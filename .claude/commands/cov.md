---
description: 커버리지 포함 테스트 실행 (pytest --cov=app)
---

`C:/msys64/mingw64/bin/python.exe -m pytest tests/ --cov=app --cov-report=term-missing` 를 실행하고:

- 전체 테스트 결과(passed/failed/skipped) 요약
- 커버리지 70% 미만인 파일만 표시 (파일명, 커버리지 %, missing 라인 범위)
- 전체 커버리지 퍼센트를 마지막에 한 줄로 표시
