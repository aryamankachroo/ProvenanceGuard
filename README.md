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
   statistics: sentence-length variance (burstiness), lexical diversity (MATTR),
   punctuation variety, and average sentence complexity.
6. **Confidence scorer** combines both into one score + verdict band
   (`likely human` / `uncertain` / `likely AI`); disagreement and short text
   lower confidence toward "uncertain".
7. **Transparency label** explains the verdict in plain, hedged language.
8. **Audit log** (structured JSON) stores the full decision.
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
`uncertain` / `likely_ai`), the calibrated `confidence` score, a per-signal
breakdown (`signals.llm`, `signals.stylometric` with its four features), a
`reliability` factor, and the transparency `label` (its text varies with the
confidence score). Save the `content_id` — you need it to file an appeal. Each
submission writes a structured entry (both signal scores + combined result) to
`audit_log.json`, readable via `GET /log`.

Appeal a verdict with the `content_id` from any `/submit` response:

```bash
curl -s -X POST http://127.0.0.1:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id":"PASTE-CONTENT-ID-HERE","creator_reasoning":"I wrote this myself from personal experience."}'
```

The appeal sets the content's status to `under_review` and populates
`appeal_reasoning` on its audit-log entry (verify with `GET /log`).

To test the detection pipeline directly (both signals + scoring self-test):

```bash
python detection.py
```

## Rate limiting

`POST /submit` is limited to **10 requests/minute and 100/day per client IP**
(via Flask-Limiter, in-memory storage). Reasoning: a genuine creator checking
their own work submits a handful of times, so 10/minute never gets in their way,
while 100/day caps a single client's LLM-API cost. The limit sits on `/submit`
(the expensive, abusable endpoint that calls Groq); `/log`, `/appeals`, and
`/health` are exempt, and `/appeal` gets a looser 20/minute. `429` is returned
once the limit is exceeded.

Evidence — 12 rapid requests against the 10/minute limit:

```
request 1  -> HTTP 200
request 2  -> HTTP 200
request 3  -> HTTP 200
request 4  -> HTTP 200
request 5  -> HTTP 200
request 6  -> HTTP 200
request 7  -> HTTP 200
request 8  -> HTTP 200
request 9  -> HTTP 200
request 10 -> HTTP 200
request 11 -> HTTP 429
request 12 -> HTTP 429
```

## API endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/submit` | Run both detection signals; return attribution, confidence, label |
| `POST` | `/appeal` | Dispute a verdict (`content_id` + `creator_reasoning`) → `under_review` |
| `GET` | `/log` | Return recent audit-log entries as JSON |
| `GET` | `/appeals` | Reviewer queue: entries with an appeal filed |
| `GET` | `/health` | Health check |

See [`planning.md`](./planning.md) for full request/response contracts.

> **Note on field names:** the milestone handouts use `content_id` / `creator_id`
> / `attribution` / `confidence` / `creator_reasoning`; `planning.md` was written
> with `submission_id` / `verdict` / `score`. The code follows the handout names;
> the scoring logic (thresholds, clamping) follows `planning.md`.

## Status

All milestones complete. **M1–2:** architecture + implementation-ready spec
([`planning.md`](./planning.md)). **M3:** Flask app, `POST /submit`, LLM signal,
structured audit log, `GET /log`. **M4:** stylometric signal + calibrated
two-signal confidence scoring. **M5 (production layer):** confidence-driven
transparency labels (3 variants), `POST /appeal`, rate limiting, and a complete
audit log capturing timestamp, content ID, attribution, confidence, both signal
scores, and appeal status.
