#!/bin/bash
# Stop hook: ruff + pytest 검증. 실패 시 Claude에게 재작업 지시.
# 무한 루프 방지: stop_hook_active=true 면 그냥 통과.

set -e

PY=${PYTHON_CMD:-C:/Project/데모/doc-auto-mvp/.venv-ai/Scripts/python.exe}
RUFF=${RUFF_CMD:-C:/Project/데모/doc-auto-mvp/.venv-ai/Scripts/ruff.exe}

# 긴급 우회
if [ "$SKIP_STOP_CHECKS" = "1" ]; then
    exit 0
fi

# stdin JSON 파싱 — stop_hook_active 체크
INPUT=$(cat)
ACTIVE=$("$PY" -c "import json,sys; d=json.loads(sys.argv[1] or '{}'); print(d.get('stop_hook_active', False))" "$INPUT" 2>/dev/null || echo "False")
if [ "$ACTIVE" = "True" ]; then
    exit 0
fi

FAIL=0
MSG=""

# 1) ruff
if [ -x "$RUFF" ]; then
    RUFF_OUT=$("$RUFF" check app/ 2>&1)
    if [ $? -ne 0 ]; then
        FAIL=1
        MSG="${MSG}[ruff 실패]
${RUFF_OUT}

"
    fi
fi

# 2) pytest (-x: 첫 실패 시 중단, 빠름)
PYTEST_OUT=$("$PY" -m pytest tests/ -x -q --no-header 2>&1 | tail -20)
if echo "$PYTEST_OUT" | grep -qE "failed|error"; then
    FAIL=1
    MSG="${MSG}[pytest 실패]
${PYTEST_OUT}
"
fi

if [ $FAIL -eq 1 ]; then
    # decision=block: Claude에게 계속 작업하라고 지시
    "$PY" -c "
import json, sys
msg = sys.argv[1]
print(json.dumps({'decision': 'block', 'reason': 'Stop 검증 실패 - 아래 문제 수정 후 다시 완료:\n' + msg}))
" "$MSG"
fi

exit 0
