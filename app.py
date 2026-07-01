"""Provenance Guard — Flask application.

Endpoints:
  POST /submit   -> run both detection signals, return attribution + calibrated
                    confidence + transparency label; write an audit-log entry.
  POST /appeal   -> a creator disputes a verdict; sets status to "under_review".
  GET  /log      -> recent audit-log entries as JSON.
  GET  /appeals  -> reviewer queue of submissions with an appeal filed.
  GET  /health   -> liveness check.
"""

import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import audit_log
from detection import combine, detect_llm, detect_stylometric
from labels import make_label

load_dotenv()

app = Flask(__name__)
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri="memory://",
)

MAX_TEXT_CHARS = 20000


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


@app.get("/")
@limiter.exempt
def index():
    return jsonify(
        {
            "service": "Provenance Guard",
            "description": "AI-text detection with two independent signals, "
            "calibrated confidence, transparency labels, and appeals.",
            "endpoints": {
                "POST /submit": "Run detection on text (body: text, creator_id)",
                "POST /appeal": "Dispute a verdict (body: content_id, creator_reasoning)",
                "GET /log": "Recent audit-log entries",
                "GET /appeals": "Reviewer queue of appealed items",
                "GET /health": "Liveness check",
            },
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
@limiter.limit("10 per minute;100 per day")
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

    # Signal 1 (semantic, LLM) — requires network; may fail.
    try:
        signal1 = detect_llm(text)
    except RuntimeError as exc:
        return jsonify({"error": "Detection signal unavailable.", "detail": str(exc)}), 502

    # Signal 2 (structural, stylometric) — pure Python, always available.
    signal2 = detect_stylometric(text)

    llm_score = signal1["llm_score"]
    stylometric_score = signal2["stylometric_score"]
    word_count = len(text.split())

    # Calibrated two-signal confidence (planning.md section 1-2).
    combined = combine(llm_score, stylometric_score, word_count)
    confidence = combined["score"]
    attribution = combined["attribution"]

    content_id = str(uuid.uuid4())
    timestamp = _now_iso()
    label = make_label(confidence)

    audit_log.log_entry(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "attribution": attribution,
            "confidence": confidence,
            "llm_score": llm_score,
            "stylometric_score": stylometric_score,
            "reliability": combined["reliability"],
            "features": signal2["features"],
            "label": label,
            "status": "classified",
            "appeal_filed": False,
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "attribution": attribution,
            "confidence": confidence,
            "signals": {
                "llm": {"score": llm_score, "rationale": signal1["rationale"]},
                "stylometric": {
                    "score": stylometric_score,
                    "features": signal2["features"],
                },
            },
            "reliability": combined["reliability"],
            "label": label,
            "status": "classified",
            "timestamp": timestamp,
        }
    )


@app.post("/appeal")
@limiter.limit("20 per minute")
def appeal():
    body = request.get_json(silent=True) or {}
    content_id = body.get("content_id")
    # Handout uses `creator_reasoning`; accept `reason` as an alias too.
    creator_reasoning = body.get("creator_reasoning") or body.get("reason")

    if not isinstance(content_id, str) or not content_id.strip():
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if not isinstance(creator_reasoning, str) or not creator_reasoning.strip():
        return jsonify({"error": "Field 'creator_reasoning' is required."}), 400

    original = audit_log.find_by_content_id(content_id)
    if original is None:
        return jsonify({"error": f"Unknown content_id: {content_id}"}), 404

    appeal_id = str(uuid.uuid4())
    timestamp = _now_iso()

    # Update the original classification record in place: flip its status and
    # attach the appeal so it travels alongside the original decision in the log.
    updated = audit_log.update_entry(
        content_id,
        {
            "status": "under_review",
            "appeal_filed": True,
            "appeal_id": appeal_id,
            "appeal_reasoning": creator_reasoning,
            "appeal_timestamp": timestamp,
        },
    )

    return jsonify(
        {
            "appeal_id": appeal_id,
            "content_id": content_id,
            "status": "under_review",
            "appeal_reasoning": creator_reasoning,
            "message": "Appeal received. This content is now under review.",
            "original_attribution": updated.get("attribution"),
            "original_confidence": updated.get("confidence"),
            "timestamp": timestamp,
        }
    )


@app.get("/log")
@limiter.exempt
def log():
    return jsonify({"entries": audit_log.get_recent()})


@app.get("/appeals")
@limiter.exempt
def appeals():
    queue = [e for e in audit_log.get_recent(limit=1000) if e.get("appeal_filed")]
    return jsonify({"appeals": queue})


if __name__ == "__main__":
    # Port is configurable (macOS AirPlay Receiver often occupies 5000).
    port = int(os.getenv("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)
