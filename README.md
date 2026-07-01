# Provenance Guard

An AI-text-detection service. A creator submits a piece of text; Provenance
Guard runs it through **two independent detection signals**, combines them into a
single **calibrated confidence score**, attaches a **human-readable transparency
label**, records the decision in an **audit log**, and lets the creator **appeal**
a verdict they disagree with. Every endpoint is protected by **rate limiting**.

The guiding principle behind every design decision here: **detection is
probabilistic, not certain.** The system is built to surface uncertainty
honestly and to protect a wrongly-flagged human with a clear appeal path — never
to hand down a confident accusation on thin evidence.

> The full design spec — architecture narrative, per-signal analysis,
> false-positive handling, API contract, and flow diagrams — lives in
> [`planning.md`](./planning.md).

---

## Table of contents

- [Architecture](#architecture)
- [How a submission flows through the system](#how-a-submission-flows-through-the-system)
- [Detection signals — and why these two](#detection-signals--and-why-these-two)
- [Confidence scoring — and why this approach](#confidence-scoring--and-why-this-approach)
- [Worked examples (real scores)](#worked-examples-real-scores)
- [Transparency labels — the three variants](#transparency-labels--the-three-variants)
- [Appeals workflow](#appeals-workflow)
- [Rate limiting](#rate-limiting)
- [Audit log](#audit-log)
- [Setup & running](#setup--running)
- [API endpoints](#api-endpoints)
- [Known limitations](#known-limitations)
- [Spec reflection](#spec-reflection)
- [AI usage](#ai-usage)
- [Portfolio walkthrough](#portfolio-walkthrough)
- [Project status](#project-status)

---

## Architecture

```
                         (over quota → 429)
 raw text    ┌────────────────────────────────────────────┐
 [Client] ───┴─► [Rate limiter] ──ok──► [Input validator]
                                              │  │ valid text
                    (invalid → 400) ◄──────────┘  │
                                                  ▼
                            ┌─────────────────────┴──────────────────┐
                            ▼                                        ▼
              [Signal 1: LLM classifier (Groq)]      [Signal 2: stylometric analyzer]
                            │ s1 + rationale             s2 + features │
                            └───────────────┬──────────────────────────┘
                                            ▼
                                 [Confidence scorer]  → calibrated score + verdict
                                            ▼
                              [Transparency label generator]
                                            ▼
                                   [Audit log (JSON)]  → response to client

 Appeal:  [Client] --content_id + reasoning--> [Appeal handler] --lookup/update-->
          [Audit log]  (status: classified → under_review)  --> confirmation
```

The codebase mirrors this diagram:

| File | Responsibility |
| --- | --- |
| `app.py` | Flask app, routing, rate limiter, request validation |
| `detection.py` | Signal 1 (`detect_llm`), Signal 2 (`detect_stylometric`), scoring (`combine`) |
| `labels.py` | Transparency label generation (`make_label`) |
| `audit_log.py` | Structured JSON audit store (append / read / update) |

## How a submission flows through the system

1. **`POST /submit`** → Flask receives the text and `creator_id`.
2. **Rate limiter** (Flask-Limiter) enforces per-client quota; over quota → `429`.
3. **Input validator** checks the text is present, non-empty, and within bounds.
4. **Signal 1 — LLM classifier** (Groq `llama-3.3-70b-versatile`) judges semantic
   / stylistic coherence and returns a probability `s1` + a rationale.
5. **Signal 2 — stylometric heuristics** (pure Python) measures structural
   statistics and returns a score `s2` + the underlying features.
6. **Confidence scorer** (`combine`) blends the two and damps toward "uncertain"
   when the text is short or the signals disagree.
7. **Transparency label generator** turns the score into hedged, plain-English text.
8. **Audit log** (structured JSON) stores the full decision.
9. **Response** returns the attribution, confidence, per-signal breakdown, and label.

An **appeal** (`POST /appeal`) looks the submission up by `content_id`, flips its
status to `under_review`, attaches the appellant's reasoning to the audit-log
entry, and confirms back to the creator.

---

## Detection signals — and why these two

The system needs two signals that are **genuinely independent**. If both signals
measured the same property, agreement between them would tell us nothing new. So
one signal is **semantic** (what the text *means* and how it *reads*) and the
other is **structural** (measurable statistics about the text's shape). Their
strengths and blind spots barely overlap, which is exactly what makes combining
them more informative than either alone.

### Signal 1 — LLM classification (Groq `llama-3.3-70b-versatile`)

- **What it measures:** holistic semantic and stylistic coherence — does the
  text *read* as machine-generated (generic phrasing, even tone, hedging, an
  absence of personal voice)?
- **Why this signal:** modern LLMs are extremely good at recognizing their own
  register — the fluent, evenly-structured, slightly generic "voice" of AI
  writing. This is very hard to capture with hand-written rules.
- **Output:** a JSON object `{ai_probability, rationale}`, clamped to
  `[0.05, 0.95]` so a single non-deterministic model can never claim absolute
  certainty. `temperature=0` for repeatability.
- **Blind spot:** non-deterministic and prompt-sensitive; can be fooled by AI
  text a human lightly edited; can wrongly flag formal, generic, or
  non-native-English human writing; inherits the model's own biases.

### Signal 2 — Stylometric heuristics (pure Python)

Four structural features, each mapped to a 0–1 "AI-likeness" sub-score, combined
as a weighted average:

| Feature | What it measures | Direction | Weight |
| --- | --- | --- | --- |
| **Burstiness** | Coefficient of variation of sentence lengths | low variance ⇒ AI-like | 0.45 |
| **Lexical diversity** | MATTR (length-controlled type-token ratio) | low diversity ⇒ AI-like | 0.20 |
| **Punctuation variety** | Count of distinct punctuation types used | fewer types ⇒ AI-like | 0.15 |
| **Avg sentence complexity** | Mean words per sentence | ~20 wps ⇒ AI-like | 0.20 |

- **Why this signal:** it captures something the LLM can't quantify — the
  *rhythm* of writing. Human prose is "bursty": a long sentence, then a short
  one, then a fragment. AI prose tends to be metronomic. Burstiness is the
  strongest of the four features, which is why it carries the most weight.
- **Why pure Python:** it's free, instant, deterministic, and independent of any
  model — a good complement to the paid, stochastic LLM call.
- **Blind spot:** blind to meaning entirely; unreliable on short text (too little
  data for stable statistics); genre-dependent (technical/legal writing is
  uniform regardless of author); gameable by deliberately varying sentence length.

---

## Confidence scoring — and why this approach

Both signals produce a 0–1 score. Naively averaging them would be a mistake,
because they don't deserve equal trust in every situation. The scoring happens
in two stages (see `combine()` in `detection.py`):

**Stage A — weighted blend.** The LLM is the stronger semantic judge, so it gets
more weight:

```
raw = 0.6·s1 + 0.4·s2
```

**Stage B — reliability damping.** Thin or conflicting evidence should *not*
produce a confident verdict, so we pull the score toward the "uncertain" center
(0.5) when the text is short or the two signals disagree:

```
length_factor    = clamp(word_count / 100, 0.35, 1.0)
disagreement     = abs(s1 − s2)
agreement_factor = 1 − 0.5·clamp((disagreement − 0.2) / 0.6, 0, 1)
reliability      = length_factor · agreement_factor
score            = 0.5 + (raw − 0.5)·reliability
```

**Why this and not a threshold at 0.5?** A binary flip at 0.5 would turn a
0.51 into a confident "AI" verdict and a 0.49 into a confident "human" one —
absurd for a difference that small. Instead the score maps to **three bands**:

| `score` | Verdict |
| --- | --- |
| `< 0.40` | `likely_human` |
| `0.40 – 0.70` | `uncertain` |
| `≥ 0.70` | `likely_ai` |

The bands are **asymmetric on purpose**: the "AI" verdict requires a high bar
(0.70) because a false AI accusation is the most damaging error the system can
make. It is deliberately *easier* to land in "uncertain" than to be labeled AI.

**What I'd change deploying this for real:**
- **Calibrate against a large labeled corpus.** The current anchors were tuned
  against a handful of samples; real deployment needs thousands of labeled
  human/AI texts to set anchors, weights, and thresholds statistically.
- **Sample the LLM multiple times** (or use an ensemble) to average out its
  non-determinism instead of relying on a single call.
- **Never auto-penalize.** A verdict should inform a human moderator, not
  trigger an automatic takedown — the appeal path assumes a human in the loop.

---

## Worked examples (real scores)

These are actual outputs from the running system, showing that the scoring
produces **meaningful variation**, not a constant. (Lifted from Milestone 4/5
testing.)

### High-confidence case — clearly AI-generated

> *"In today's rapidly evolving digital landscape, organizations must prioritize
> the adoption of innovative technologies to remain competitive. It is important
> to recognize that digital transformation is not merely a technological
> shift..."*

| Field | Value |
| --- | --- |
| Signal 1 (LLM) | **0.80** |
| Signal 2 (stylometric) | **0.92** (burstiness CV 0.055 — extremely uniform) |
| Reliability | 0.79 |
| **Combined confidence** | **0.77** |
| **Verdict** | **`likely_ai`** |

Both signals agree strongly and the text is long enough to trust, so the score
lands confidently in the AI band.

### Lower-confidence case — formal human writing

> *"The relationship between monetary policy and asset price inflation has been
> extensively studied in the literature. Central banks face a fundamental
> tension between their mandate for price stability..."*

| Field | Value |
| --- | --- |
| Signal 1 (LLM) | **0.70** |
| Signal 2 (stylometric) | **0.85** (burstiness CV 0.256 — uniform, formal) |
| Reliability | 0.43 |
| **Combined confidence** | **0.61** |
| **Verdict** | **`uncertain`** |

This is a human-written passage whose *formal, uniform structure* looks AI-like
to the stylometric signal. Rather than flag it, the system's short-text damping
and the 0.70 AI threshold hold it in the **uncertain** band — the false-positive
protection working as designed. Confidence here (0.61) is noticeably lower and in
a different band than the AI case (0.77).

---

## Transparency labels — the three variants

The label text a user sees is **derived from the confidence score** — it is never
the same text regardless of score. `{pct}` is `round(score × 100)`. The exact
text of all three variants (`make_label()` in `labels.py`):

### High-confidence AI (`score ≥ 0.70`)

> **Likely AI-generated (AI-likelihood: {pct}%).** Both our writing-style
> analysis and our language-model reviewer found patterns strongly associated
> with AI-generated text — such as unusually uniform sentence structure and
> generic phrasing. This is an automated assessment and can be wrong. If you
> wrote this yourself, you can appeal this result.

### High-confidence human (`score < 0.40`)

> **Likely human-written (AI-likelihood: {pct}%).** Our analysis found the
> natural variation in sentence length, vocabulary, and phrasing that is typical
> of human writing. This is an automated assessment, not a guarantee.

### Uncertain (`0.40 ≤ score < 0.70`)

> **Inconclusive (AI-likelihood: {pct}%).** Our two detectors disagreed or found
> mixed evidence, so we cannot confidently say whether this text was written by a
> human or by AI. We are NOT flagging this as AI-generated. If a decision depends
> on this result, please treat it as undetermined.

All three are reachable with real inputs (verified: `likely_ai` at 77%,
`likely_human` at 35%, `uncertain` at 61%).

---

## Appeals workflow

- **Who can appeal:** the creator who received the `content_id` from `/submit`.
  *(Prototype limitation: no authentication, so possession of the `content_id`
  is the only credential — see [Known limitations](#known-limitations).)*
- **What they provide:** `content_id` + `creator_reasoning`.
- **What happens:** the original classification record's status flips
  `classified → under_review`, and the appeal (`appeal_id`, `appeal_reasoning`,
  `appeal_timestamp`, `appeal_filed: true`) is attached to that same audit-log
  entry so it travels alongside the original decision. `GET /appeals` returns the
  reviewer queue of everything with an appeal filed.
- Automated re-classification is intentionally **out of scope** — an appeal flags
  content for a human reviewer; it doesn't let the machine re-judge itself.

---

## Rate limiting

`POST /submit` is limited to **10 requests/minute and 100/day per client IP**
(Flask-Limiter, in-memory storage). **Reasoning:** a genuine creator checking
their own work submits a handful of times, so 10/minute never gets in their way,
while 100/day caps any single client's LLM-API cost and blocks a script trying to
flood the system. The limit sits on `/submit` because that's the expensive,
abusable endpoint (it calls the Groq API); `/log`, `/appeals`, and `/health` are
exempt, and `/appeal` gets a looser 20/minute.

**Evidence** — 12 rapid requests against the 10/minute limit:

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

---

## Audit log

Every submission writes one structured JSON entry; an appeal updates that same
entry in place. The log captures **timestamp, content ID, attribution,
confidence, both individual signal scores, and appeal status** — everything
needed to reconstruct or review a decision. A real entry (after an appeal):

```json
{
  "content_id": "0e74adf6-d644-44f5-aeeb-d8e5b93bd2bb",
  "creator_id": "user-ai",
  "timestamp": "2026-07-01T00:47:43.639162+00:00",
  "attribution": "likely_ai",
  "confidence": 0.7734,
  "llm_score": 0.8,
  "stylometric_score": 0.9153,
  "reliability": 0.79,
  "features": {
    "burstiness_cv": 0.0552,
    "mattr": 0.8407,
    "punctuation_variety": 2,
    "avg_sentence_length": 19.75
  },
  "label": "Likely AI-generated (AI-likelihood: 77%). ...",
  "status": "under_review",
  "appeal_filed": true,
  "appeal_id": "bd1611ab-5296-4a82-93cb-5f7088b8d20b",
  "appeal_reasoning": "I wrote this myself from personal experience running my company.",
  "appeal_timestamp": "2026-07-01T00:47:44.973089+00:00"
}
```

Read the log any time with `GET /log`.

---

## Setup & running

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

Run the server:

```bash
python app.py            # serves on http://127.0.0.1:5000
```

Exercise it with curl:

```bash
# health
curl -s http://127.0.0.1:5000/health

# submit (requires text + creator_id) — returns content_id, attribution, confidence, label
curl -s -X POST http://127.0.0.1:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text":"The sun dipped below the horizon, painting the sky in amber and rose.","creator_id":"test-user-1"}'

# appeal a verdict (use a content_id from a /submit response)
curl -s -X POST http://127.0.0.1:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id":"PASTE-CONTENT-ID","creator_reasoning":"I wrote this myself."}'

# audit log / reviewer queue
curl -s http://127.0.0.1:5000/log
curl -s http://127.0.0.1:5000/appeals
```

Test the detection pipeline directly (both signals + the scoring self-test that
verifies `combine()` against `planning.md`'s worked-examples table):

```bash
python detection.py
```

## API endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/submit` | Run both signals; return attribution, confidence, label |
| `POST` | `/appeal` | Dispute a verdict (`content_id` + `creator_reasoning`) → `under_review` |
| `GET` | `/log` | Recent audit-log entries as JSON |
| `GET` | `/appeals` | Reviewer queue: entries with an appeal filed |
| `GET` | `/health` | Health check |

---

## Known limitations

The system has real, predictable failure modes — each tied to a property of a
specific signal, not a generic "needs more data."

- **Formal / uniform human writing (academic, legal, technical, corporate).**
  This is the clearest weakness, and I can show it in my own test data: a
  human-written monetary-policy passage scored **0.85 on the stylometric signal**
  because its burstiness CV was only 0.256 — formal prose is deliberately even
  and precise, exactly what the burstiness feature reads as "AI-like." It was
  only saved from a false AI flag by the LLM's disagreement and the short-text
  damping (landing at 0.61 `uncertain`). Longer formal human text would push
  higher. **Root cause:** the burstiness feature equates uniformity with machine
  authorship, but some humans write uniformly by discipline or genre.
- **Non-native / ESL writing.** Simpler, more uniform sentence structure and a
  smaller working vocabulary push *both* signals toward AI-like — a genuine
  fairness risk of systematically misjudging a vulnerable group. Same root cause
  on the structural side (burstiness/diversity), compounded by the LLM's biases.
- **Very short text.** Under ~40 words the stylometric statistics are unstable;
  the reliability damping deliberately forces such inputs toward `uncertain`
  rather than guessing.
- **Human-edited AI ("hybrid") text.** Authorship is genuinely ambiguous; the
  system correctly reports `uncertain` rather than pretending to know.

---

## Spec reflection

**One way the spec guided the implementation.** The worked-examples table in
`planning.md` §2 gave exact expected scores for four scenarios (clearly-AI,
clearly-human, conflicting, too-short). I turned that table directly into a
`combine()` self-test (`_selftest_combine()` in `detection.py`). Because the spec
committed to concrete numbers *before* any code existed, I could immediately
verify the scoring function matched the intended thresholds — and the test caught
exactly the kind of silent divergence the handout warned about.

**One way the implementation diverged from the spec — and why.** The spec's
stylometric anchors were provisional guesses (MATTR human/AI anchors of
0.78/0.66). When I ran real text through the signal in Milestone 4, I found the
lexical-diversity feature was **saturating to zero for every realistic input** —
short real texts have MATTR ≈ 0.87, well above the 0.78 anchor — which silently
wasted 20% of the stylometric weight and biased everything toward "human." I
recalibrated the anchors to 0.90/0.80 and loosened the length-reliability
constant from 150→100 words, then **updated `planning.md` to match** so the spec
and code never drifted apart. The divergence was driven by real data contradicting
a provisional assumption — precisely what Milestone 4's calibration step is for.

---

## AI usage

This project was built collaboratively with an AI coding assistant. Two specific
instances:

1. **Milestone 3 — Flask app + LLM signal.** I directed the AI to generate the
   Flask skeleton and a `detect_llm()` function from the detection-signals spec
   section. It produced a working route and Groq call. **I revised it** to: force
   Groq JSON mode with `temperature=0`, clamp the returned probability to
   `[0.05, 0.95]` so a single stochastic call can't claim certainty, and wrap the
   API call so a Groq failure returns a clean `502` instead of crashing the
   endpoint.
2. **Milestone 4 — stylometric signal + scoring.** I directed the AI to implement
   the four-feature stylometric signal and the two-stage `combine()` function. It
   produced reasonable-looking code, but on testing I **overrode the
   calibration**: I caught the MATTR dead-zone bug described in the spec
   reflection, recalibrated the anchors and length constant, and added the
   self-test tying `combine()` back to the spec's worked-examples table so future
   changes can't silently break the thresholds.

In both cases the AI accelerated the boilerplate, but the engineering judgment —
what the scores should mean, where the calibration was wrong, how to fail safely
— was reviewed and corrected by hand.

---

## Portfolio walkthrough

A short (~2 minute) screen recording accompanies this project. It gives a quick
tour of the system working end-to-end and talks through a few design decisions.
The detailed evidence (audit-log sample, rate-limit behavior, label variants,
appeal handling) lives in this README; the walkthrough is just a guided tour.

> **Recording link:** _add your video link here after recording._

Suggested ~2-minute script:
1. **(15s) What it is** — "Provenance Guard detects AI-generated text using two
   independent signals and, crucially, is honest about uncertainty."
2. **(30s) Submit clearly-AI text** — show the `/submit` response: `likely_ai`,
   confidence ~0.77, and the transparency label. Point out the per-signal scores.
3. **(30s) Submit borderline formal-human text** — show it landing in
   `uncertain`, and explain *why* that's the desired behavior (false-positive
   protection), referencing the two-stage scoring.
4. **(20s) Appeal it** — `POST /appeal`, then `GET /log` showing status flipped to
   `under_review` with the reasoning attached.
5. **(15s) Rate limit** — run the 12-request loop, show the `429`s.
6. **(10s) One design decision** — e.g., "I use three bands with an asymmetric
   AI threshold, because a false AI accusation is the worst error we can make."

---

## Project status

All six milestones complete.

- **M1–M2:** architecture + implementation-ready spec ([`planning.md`](./planning.md)).
- **M3:** Flask app, `POST /submit`, LLM signal, structured audit log, `GET /log`.
- **M4:** stylometric signal + calibrated two-signal confidence scoring.
- **M5 (production layer):** confidence-driven transparency labels, `POST /appeal`,
  rate limiting, complete audit log.
- **M6:** this documentation + portfolio walkthrough.
