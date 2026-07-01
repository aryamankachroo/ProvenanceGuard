# Provenance Guard

An AI-text-detection service. A creator submits text; Provenance Guard runs it
through two independent detection signals, combines them into a single
confidence score, attaches a human-readable transparency label, records the
decision in an audit log, and lets the creator appeal a verdict.

Detection is treated as **probabilistic, not certain** — the system is built to
surface uncertainty and to give a wrongly-flagged human a way to push back.

> Full architecture, signal analysis, false-positive handling, API contract,
> and flow diagrams live in [`planning.md`](./planning.md).

## How a submission flows through the system

1. **`POST /submit`** → the Flask app receives the text.
2. **Rate limiter** (Flask-Limiter) enforces per-client quota.
3. **Input validator** checks the text is present and within length bounds.
4. **Signal 1 — LLM classifier** (Groq `llama-3.3-70b-versatile`) judges
   semantic/stylistic coherence.
5. **Signal 2 — stylometric heuristics** (pure Python) measures structural
   statistics: sentence-length variance, type-token ratio, punctuation density,
   average sentence complexity.
6. **Confidence scorer** combines both into one score + verdict band
   (`likely human` / `uncertain` / `likely AI`); disagreement lowers confidence.
7. **Transparency label** explains the verdict in plain, hedged language.
8. **Audit log** (SQLite / JSON) stores the full decision.
9. **Response** returns the verdict, confidence, per-signal breakdown, and label.

An **appeal** (`POST /appeal`) looks up the submission, moves its status to
`under_review`, logs the appeal, and confirms back to the creator.

## Detection signals

| Signal | Measures | Blind spot |
| --- | --- | --- |
| LLM classification (Groq) | Holistic semantic & stylistic coherence | Non-deterministic; can flag formal or non-native human writing; fooled by lightly-edited AI text |
| Stylometric heuristics | Structural statistics (burstiness, lexical diversity, punctuation, complexity) | Blind to meaning; unreliable on short text; genre-dependent; gameable |

The two are independent — one semantic, one structural — so the pair is more
informative than either alone.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Mac/Linux
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt
```

Create a `.env` file in the repo root (copy from `.env.example`) — never commit it:

```
GROQ_API_KEY=your_key_here
```

## Running the app (Milestone 3)

```bash
source .venv/bin/activate
python app.py            # serves on http://127.0.0.1:5000
```

Try it with curl:

```bash
# health check
curl -s http://127.0.0.1:5000/health

# submit text for detection (requires text + creator_id)
curl -s -X POST http://127.0.0.1:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text":"The sun dipped below the horizon, painting the sky in hues of amber and rose.","creator_id":"test-user-1"}'

# view recent audit-log entries
curl -s http://127.0.0.1:5000/log
```

A `/submit` response includes `content_id`, `attribution` (`likely_human` /
`uncertain` / `likely_ai`), `llm_score`, a **placeholder** `confidence`, and a
**placeholder** `label`. Save the `content_id` — you'll need it for appeals in
Milestone 5. Each submission also writes a structured entry to `audit_log.json`,
readable via `GET /log`.

## API endpoints

| Method | Endpoint | Purpose | Status |
| --- | --- | --- | --- |
| `POST` | `/submit` | Run detection on a piece of text | Signal 1 live (M3) |
| `GET` | `/log` | Return recent audit-log entries | live (M3) |
| `GET` | `/health` | Health check | live (M3) |
| `POST` | `/appeal` | Dispute a previous verdict | planned (M5) |
| `GET` | `/submissions/<id>` | Fetch a stored record and its appeals | planned (M5) |

See [`planning.md`](./planning.md) for full request/response contracts.

> **Note on field names:** the milestone handouts use `content_id` / `creator_id`
> / `attribution` / `confidence`; `planning.md` was written with
> `submission_id` / `verdict` / `score`. The code follows the handout names; the
> scoring logic (thresholds, clamping) follows `planning.md`.

## Status

Milestones 1–2 complete (architecture + implementation-ready spec in
[`planning.md`](./planning.md)). **Milestone 3 complete:** Flask app with
`POST /submit`, the LLM detection signal (Groq), a structured JSON audit log,
and `GET /log`. Confidence and label are placeholders until Milestones 4–5.
