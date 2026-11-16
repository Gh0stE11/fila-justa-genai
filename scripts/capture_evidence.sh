#!/usr/bin/env bash
# Gera os logs de evidência. Uso: bash scripts/capture_evidence.sh real|mock
# "real" precisa de OPENROUTER_API_KEY no .env.
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-mock}"
AS_OF="2026-11-09"
mkdir -p evidence output
python3 -m pytest -v 2>&1 | tee evidence/pytest_output.txt
case "$MODE" in
  real)
    python3 -m citizentech_ai config | tee evidence/real_config.json
    python3 -m citizentech_ai run --llm openrouter --as-of "$AS_OF" --out output/real 2>&1 | tee evidence/run_openrouter.log
    python3 -m citizentech_ai analyze "A rua alagou e ninguém passa, tem criança na água" --llm openrouter 2>&1 | tee evidence/analyze_openrouter.log
    ;;
  mock)
    python3 -m citizentech_ai run --llm mock --as-of "$AS_OF" --out output 2>&1 | tee evidence/run_mock.log
    python3 -m citizentech_ai run --llm mock --chaos --as-of "$AS_OF" --out output/chaos 2>&1 | tee evidence/run_mock_chaos.log
    python3 -m citizentech_ai run --llm none --as-of "$AS_OF" --out output/rules_only 2>&1 | tee evidence/run_rules_only.log
    ;;
esac
