"""Provenance Guard — Flask application.

Milestone 3 scope:
  POST /submit  -> run Signal 1 (LLM), return an attribution + placeholder
                   confidence/label, and write a structured audit-log entry.
  GET  /log     -> return recent audit-log entries as JSON.
  GET  /health  -> liveness check.

Confidence and label are placeholders until Milestone 4 (second signal +
calibrated scoring) and Milestone 5 (transparency labels).
"""

import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import audit_log
from detection import attribution_for, detect_llm

load_dotenv()

app = Flask(__name__)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["60 per minute"],
)

MAX_TEXT_CHARS = 20000

PLACEHOLDER_LABEL = (
    "Placeholder label — full transparency label is added in Milestone 5."
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
@limiter.limit("20 per minute")
def submit():
    body = request.get_json(silent=True) or {}
    text = body.get("text")
    creator_id = body.get("creator_id")

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Field 'text' is required and must be non-empty."}), 400
    if not isinstance(creator_id, str) or not creator_id.strip():
        return jsonify({"error": "Field 'creator_id' is required and must be non-empty."}), 400
    if len(text) > MAX_TEXT_CHARS:
        return jsonify({"error": f"Text exceeds {MAX_TEXT_CHARS} characters."}), 400

    try:
        signal1 = detect_llm(text)
    except RuntimeError as exc:
        return jsonify({"error": "Detection signal unavailable.", "detail": str(exc)}), 502

    llm_score = signal1["llm_score"]
    # Placeholder: with only one signal, confidence mirrors the LLM score.
    # Milestone 4 replaces this with the calibrated two-signal combination.
    confidence = llm_score
    attribution = attribution_for(confidence)

    content_id = str(uuid.uuid4())
    timestamp = _now_iso()

    audit_log.log_entry(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "attribution": attribution,
            "confidence": confidence,
            "llm_score": llm_score,
            "status": "classified",
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "attribution": attribution,
            "confidence": confidence,
            "llm_score": llm_score,
            "rationale": signal1["rationale"],
            "label": PLACEHOLDER_LABEL,
            "status": "classified",
            "timestamp": timestamp,
        }
    )


@app.get("/log")
@limiter.exempt
def log():
    return jsonify({"entries": audit_log.get_recent()})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
