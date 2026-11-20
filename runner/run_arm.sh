#!/usr/bin/env bash
# Launch one pilot attempt for one arm inside the shared sandbox image.
#   runner/run_arm.sh A|B|C <attempt-label>
# Same model alias, same prompt, same wall clock for every arm. Only the arm's own
# workspace is mounted; grader-side/, calibration/, sealed/ and the live task repo are not.
set -euo pipefail
ARM="$1"; LABEL="${2:-a1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WS="${WS_OVERRIDE:-$ROOT/arms/$ARM}"
LOGS="${LOGS_OVERRIDE:-$ROOT/logs/$ARM-$LABEL}"; mkdir -p "$LOGS"
MODEL="${MODEL:-qwen25-7b-pilot}"
WALL="${WALL:-2700}"                     # 45 min
# Endpoint switch: local Ollama by default; ENDPOINT_URL + ENDPOINT_KEY_FILE for a hosted model.
OLLAMA_URL="${ENDPOINT_URL:-http://host.lima.internal:11434/v1}"
API_KEY="ollama"; [ -n "${ENDPOINT_KEY_FILE:-}" ] && API_KEY="$(cat "$ENDPOINT_KEY_FILE")"

PROMPT="${PROMPT_OVERRIDE:-}"
[ -n "$PROMPT" ] || PROMPT="Read $WS/instruction.md and complete the task it describes. Your working directory is $WS. The data is in $WS/data/. Write your submission to $WS/output/ exactly as the instruction specifies. You may use $WS/scratch/ for anything else. Work autonomously until the submission is written and you have checked that it loads and predicts on the validation data."
printf '%s\n' "$PROMPT" > "$LOGS/prompt.txt"

IMAGE="ali-bench-agent:pilot"
REQUIRED_OUTPUT="${REQUIRED_OUTPUT:-$WS/output/predictor.py}"
case "$ARM" in
  A|AF|AF2)  # OmniCLI agent (AF = + completion gate, 900 s shell; AF2 = AF + output-validation gate + work-method guidance), pinned to one provider + one model, no fallback roster.
      mkdir -p "$LOGS/omni-config"
      cat > "$LOGS/omni-config/config.json" <<JSON
{"providers": {"ollama": {"name": "Ollama (local)", "base_url": "$OLLAMA_URL",
  "env_key": "OLLAMA_API_KEY", "default_model": "$MODEL", "description": "pilot model",
  "requires_auth_for_models": false, "fallback_models": [], "verification_panel": []}},
 "leadership": {"enabled": false}}
JSON
      CMD=(bash -lc "cd '$WS' && omni agent -p ollama -m '$MODEL' --max-turns 400 --allow-shell --workspace '$WS' \"\$(cat /logs/prompt.txt)\"")
      EXTRA=(-e OMNICLI_CONFIG_DIR=/logs/omni-config -e "OLLAMA_API_KEY=$API_KEY")
      if [ "$ARM" = AF ]; then IMAGE="ali-bench-agent:omnifixed"; EXTRA+=(-e "OMNI_REQUIRED_OUTPUT=$REQUIRED_OUTPUT"); fi
      if [ "$ARM" = AF2 ]; then IMAGE="ali-bench-agent:omniaf2"; EXTRA+=(-e "OMNI_REQUIRED_OUTPUT=$REQUIRED_OUTPUT" -e "OMNI_REQUIRED_KEYS=${REQUIRED_KEYS:-}"); fi
      ;;
  B|C) # OpenCode executor with a Harness2 harness tree rendered to AGENTS.md (+ .opencode/).
      HARNESS="${HARNESS:-$ROOT/runner/armB_harness}"
      cp "$HARNESS/AGENTS.md" "$WS/AGENTS.md"
      [ -d "$HARNESS/.opencode" ] && cp -r "$HARNESS/.opencode" "$WS/.opencode"
      cat > "$WS/opencode.json" <<JSON
{"\$schema": "https://opencode.ai/config.json",
 "provider": {"ollama": {"npm": "@ai-sdk/openai-compatible", "name": "Ollama (local)",
   "options": {"baseURL": "$OLLAMA_URL", "apiKey": "{env:BENCH_API_KEY}"},
   "models": {"$MODEL": {"name": "$MODEL", "tools": true}}}},
 "model": "ollama/$MODEL"}
JSON
      PERM="{\"external_directory\": {\"*\": \"deny\", \"$WS\": \"allow\", \"$WS/**\": \"allow\"}}"
      CMD=(bash -lc "cd '$WS' && opencode run \"\$(cat /logs/prompt.txt)\" --dir '$WS' --model 'ollama/$MODEL' --title pilot-$ARM-$LABEL --format json")
      EXTRA=(-e "OPENCODE_PERMISSION=$PERM" -e OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=32000 -e "BENCH_API_KEY=$API_KEY")
      ;;
  *) echo "unknown arm $ARM"; exit 2;;
esac

echo "{\"arm\":\"$ARM\",\"label\":\"$LABEL\",\"model\":\"$MODEL\",\"wall_s\":$WALL,\"start\":\"$(date -u +%FT%TZ)\"}" > "$LOGS/meta.start.json"
START=$(date +%s)
set +e
docker run --rm --name "pilot-$ARM-$LABEL" --user root \
  -v "$WS:$WS" -v "$LOGS:/logs" "${EXTRA[@]}" \
  "$IMAGE" timeout --signal=INT --kill-after=60 "$WALL" "${CMD[@]}" \
  > "$LOGS/trace.stdout" 2> "$LOGS/trace.stderr"
RC=$?
set -e
END=$(date +%s)
# Snapshot the submission before anything else touches it.
mkdir -p "$LOGS/submission" && cp -r "$WS/output/." "$LOGS/submission/" 2>/dev/null || true
echo "{\"rc\":$RC,\"wall_used_s\":$((END-START)),\"end\":\"$(date -u +%FT%TZ)\"}" > "$LOGS/meta.end.json"
echo "arm $ARM $LABEL done rc=$RC in $((END-START))s"
