#!/usr/bin/env bash
#
# Provenance Guard — one-command demo for the portfolio walkthrough.
#
# Usage:
#   1. In one terminal, start the server:   PORT=5050 python app.py
#   2. In another terminal, run:            ./demo.sh
#
# Override the server URL with:  BASE_URL=http://127.0.0.1:5000 ./demo.sh

set -euo pipefail

BASE="${BASE_URL:-http://127.0.0.1:5050}"
PY="./.venv/bin/python"

AI_TEXT="In today's rapidly evolving digital landscape, organizations must prioritize the adoption of innovative technologies to remain competitive. It is important to recognize that digital transformation is not merely a technological shift but a fundamental change in organizational culture. Companies that successfully navigate this transition typically demonstrate strong leadership, a clear strategic vision, and a commitment to continuous improvement. Furthermore, investing in employee training and development is essential to ensure that the workforce is equipped with the necessary skills for the future."

HUMAN_TEXT="ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. my friend got the spicy version and said it was better. probably won't go back unless someone drags me there"

line() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }

submit() {
  "$PY" - "$BASE" "$1" "$2" <<'PY'
import json, sys, urllib.request
base, text, creator = sys.argv[1], sys.argv[2], sys.argv[3]
req = urllib.request.Request(
    base + "/submit",
    data=json.dumps({"text": text, "creator_id": creator}).encode(),
    headers={"Content-Type": "application/json"},
)
resp = json.load(urllib.request.urlopen(req))
print(json.dumps(resp, indent=2))
print("CONTENT_ID=" + resp["content_id"], file=sys.stderr)
PY
}

line "1. Submit clearly-AI text  (expect: likely_ai)"
AI_OUT=$(submit "$AI_TEXT" "demo-ai" 2> /tmp/pg_ai_cid.txt)
echo "$AI_OUT"
AI_CID=$(sed 's/CONTENT_ID=//' /tmp/pg_ai_cid.txt)

line "2. Submit clearly-human text  (expect: likely_human)"
submit "$HUMAN_TEXT" "demo-human"

line "3. Appeal the AI verdict  (content_id=$AI_CID)"
"$PY" - "$BASE" "$AI_CID" <<'PY'
import json, sys, urllib.request
base, cid = sys.argv[1], sys.argv[2]
req = urllib.request.Request(
    base + "/appeal",
    data=json.dumps({"content_id": cid,
                     "creator_reasoning": "I wrote this myself from personal experience."}).encode(),
    headers={"Content-Type": "application/json"},
)
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=2))
PY

line "4. Audit log  (note the appealed entry now shows status: under_review)"
curl -s "$BASE/log" | "$PY" -m json.tool

line "Demo complete."
echo "Tip: open $BASE/log in a browser to show the log visually,"
echo "and run the rate-limit loop last (it uses up the 10/min budget)."
