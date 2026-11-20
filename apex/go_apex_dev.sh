#!/usr/bin/env bash
# APEX dev slice (9 tasks) x arms B and AF2, all on Mistral Large 4; judge Kimi K3.1 Pro.
cd "$(dirname "$0")/.."
export MODEL="mistralai/mistral-large-4-1t-a52b" JUDGE_MODEL="moonshotai/kimi-k3.1-pro"
until docker image inspect apex-env:local >/dev/null 2>&1; do sleep 20; done
echo "[$(date +%H:%M:%S)] APEX DEV START"
i=0
for idx in $(python3 -c "import json;print(' '.join(str(t['index']) for t in json.load(open('apex/SPLIT.json'))['dev']))"); do
  i=$((i+1)); if [ $((i%2)) -eq 1 ]; then arms="B AF2"; else arms="AF2 B"; fi
  for arm in $arms; do
    .venv-bench/bin/python apex/run_apex.py --arm $arm --task-index $idx --wall 1200 --port 8090 --tag apex-dev 2>&1 | grep -E "\[APEX\]|Traceback|Error" 
  done
done
echo "[$(date +%H:%M:%S)] APEX DEV COMPLETE"
