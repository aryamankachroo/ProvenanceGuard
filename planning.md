# Provenance Guard — Spec & Plan

Provenance Guard is an AI-text-detection service. A creator submits text; the
system runs it through two independent detection signals, combines them into a
single calibrated confidence score, attaches a human-readable transparency
label, records the decision in an audit log, and lets the creator appeal.

**Guiding principle:** detection is *probabilistic, not certain*. Every number,
threshold, and label below is designed to surface uncertainty honestly and to
protect a wrongly-flagged human with a clear appeal path. Nothing in this system
is a binary flip at 0.5.

> This document is the contract that the Milestone 3–5 implementation is built
> against, and the primary artifact pasted into AI tools when generating code.

---

## The seven features (assumption — confirm against the handout)

The handout's "required features list" wasn't in the material provided, so this
is inferred from the two flows and the tool stack. **Confirm or correct it** —
everything downstream depends on it.

1. **Text submission & detection** — `POST /submit`.
2. **Detection signal 1 — LLM classification** (Groq `llama-3.3-70b-versatile`).
3. **Detection signal 2 — stylometric heuristics** (pure Python).
4. **Confidence scoring** — combine signals into one calibrated score + verdict band.
5. **Transparency label** — human-readable explanation of the verdict and its uncertainty.
6. **Audit log** — durable record of every submission and appeal decision.
7. **Appeal mechanism** — `POST /appeal`.

Rate limiting (Flask-Limiter) is a cross-cutting concern protecting every
endpoint, not one of the seven features.

---

## 1. Detection signals

Two deliberately **independent** signals: one judges *meaning and voice*
(semantic), the other judges *structure and statistics* (structural). That
independence is what makes the pair more informative than either alone.

### Signal 1 — LLM classification (Groq `llama-3.3-70b-versatile`)

- **Measures:** holistic semantic & stylistic coherence — does the text *read*
  as machine-generated (generic phrasing, even tone, hedging, no personal voice)?
- **Output:** a JSON object `{ "ai_probability": <float 0–1>, "rationale": "<1–2 sentences>" }`.
  We prompt the model to return *only* this JSON. `ai_probability` is the
  model's estimated probability the text is AI-generated.
- **Normalization:** clamp to `[0.05, 0.95]` so the model can never express
  absolute certainty (it is non-deterministic and overconfident at the extremes).
  Call this **`s1`**.
- **Blind spot:** non-deterministic and prompt-sensitive; can be fooled by
  lightly-edited AI text; can wrongly flag formal, generic, or non-native-English
  human writing; inherits the model's biases; costs latency/API calls.

### Signal 2 — Stylometric heuristics (pure Python)

Computes four structural features, maps each to a sub-score in `[0,1]` (1 =
AI-like), and takes a weighted average. Call the result **`s2`**.

| Feature | What it computes | Sub-score mapping (1 = AI-like) | Weight |
| --- | --- | --- | --- |
| **Burstiness** (primary) | Coefficient of variation of sentence lengths in words, `CV = stdev/mean` | `clamp((0.55 − CV) / (0.55 − 0.25), 0, 1)` — low variance ⇒ AI-like | 0.45 |
| **Lexical diversity** | MATTR (moving-average type-token ratio, window 50) — length-controlled | `clamp((0.78 − MATTR) / (0.78 − 0.66), 0, 1)` — low diversity ⇒ AI-like | 0.20 |
| **Punctuation variety** | Count of distinct punctuation types used, from `. , ; : — ! ? ()` | `clamp((5 − distinct) / (5 − 2), 0, 1)` — fewer types ⇒ AI-like | 0.15 |
| **Avg sentence complexity** | Mean words per sentence | `clamp(1 − abs(mean_len − 20) / 15, 0, 1)` — ~20 wps ⇒ AI-like | 0.20 |

```
s2 = 0.45·sub_burstiness + 0.20·sub_diversity + 0.15·sub_punctuation + 0.20·sub_complexity
```

- **Note on choices:** I use *punctuation variety* rather than raw punctuation
  density — variety discriminates human vs. AI better. Burstiness is the
  strongest single feature; the other three supplement it.
- **All anchor values (0.55, 0.25, 0.78, 0.66, etc.) are provisional** and get
  calibrated in M4 against a small corpus of clearly-AI and clearly-human text.
- **Blind spot:** blind to meaning; unreliable on short text (too little data
  for stable statistics); genre-dependent (technical/legal writing is uniform
  regardless of author); gameable by varying sentence length.

### Combining into a single confidence score

`s1` and `s2` are both in `[0,1]`. They combine in two stages.

**Stage A — weighted blend** (the LLM is the stronger semantic signal):

```
raw = 0.6·s1 + 0.4·s2
```

**Stage B — reliability damping toward 0.5.** Thin evidence (short text) or
conflicting signals should *not* produce a confident verdict, so we pull the
score toward the uncertain center (0.5):

```
length_factor   = clamp(word_count / 150, 0.3, 1.0)
disagreement    = abs(s1 − s2)
agreement_factor = 1 − 0.5·clamp((disagreement − 0.2) / 0.6, 0, 1)
reliability      = length_factor · agreement_factor
score            = 0.5 + (raw − 0.5)·reliability        # clamp to [0,1]
```

`score` ∈ `[0,1]` is the **single calibrated confidence score** the rest of the
system uses. Higher = more likely AI-generated.

---

## 2. Uncertainty representation

### What the score means

`score` is the system's **calibrated estimate of the probability the text was
AI-generated**: `0` = confidently human, `1` = confidently AI, `0.5` = maximally
uncertain.

**A score of 0.6 means:** the evidence leans slightly toward AI but is *not
decisive*. It falls inside the **uncertain** band, so the system will **not**
assert AI authorship — it shows an inconclusive label and recommends treating
the result as undetermined.

### Raw → calibrated mapping

- **`s1` (LLM):** the model returns a probability directly; we recalibrate only
  by clamping to `[0.05, 0.95]` to remove false certainty.
- **`s2` (stylometric):** raw weighted sub-score, already in `[0,1]`.
- **Blend + damping:** Stages A and B above. The damping step is the
  calibration that matters most — it guarantees short or conflicting inputs
  cannot earn a confident label.

### Thresholds (verdict bands)

| `score` range | Verdict | Band width |
| --- | --- | --- |
| `score < 0.40` | **likely human** | 0.40 |
| `0.40 ≤ score < 0.70` | **uncertain** | 0.30 |
| `score ≥ 0.70` | **likely AI** | 0.30 |

**Why asymmetric?** The "likely AI" verdict requires a high bar (0.70) because a
false AI accusation is the most damaging error. It is deliberately *easier* to
land in "uncertain" than to be labeled AI. This is the false-positive protection
expressed as numbers.

### Worked examples (these double as M4 test cases)

| Case | `s1` | `s2` | words | `raw` | reliability | `score` | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Clearly AI | 0.90 | 0.80 | 300 | 0.86 | 1.00 | **0.86** | likely AI |
| Clearly human | 0.10 | 0.15 | 300 | 0.12 | 1.00 | **0.12** | likely human |
| Signals conflict | 0.85 | 0.20 | 300 | 0.59 | 0.625 | **0.56** | uncertain |
| Too short | 0.80 | 0.75 | 40 | 0.78 | 0.30 | **0.58** | uncertain |

The last two are the point of the design: conflicting evidence and thin evidence
both resolve to *uncertain*, never to a confident flag.

---

## 3. Transparency label design

Each label has three parts: a **headline verdict**, a **plain-English
explanation**, and a **disclaimer/appeal pointer**. `{pct}` = `round(score·100)`.
The three variants below are the complete set the UI can ever show.

### Variant A — high-confidence AI (`score ≥ 0.70`)

Template:
```
Likely AI-generated (AI-likelihood: {pct}%).
Both our writing-style analysis and our language-model reviewer found patterns
strongly associated with AI-generated text — such as unusually uniform sentence
structure and generic phrasing.
This is an automated assessment and can be wrong. If you wrote this yourself,
you can appeal this result.
```
Rendered at `score = 0.86`:
> **Likely AI-generated (AI-likelihood: 86%).** Both our writing-style analysis
> and our language-model reviewer found patterns strongly associated with
> AI-generated text — such as unusually uniform sentence structure and generic
> phrasing. This is an automated assessment and can be wrong. If you wrote this
> yourself, you can appeal this result.

### Variant B — high-confidence human (`score < 0.40`)

Template:
```
Likely human-written (AI-likelihood: {pct}%).
Our analysis found the natural variation in sentence length, vocabulary, and
phrasing that is typical of human writing.
This is an automated assessment, not a guarantee.
```
Rendered at `score = 0.12`:
> **Likely human-written (AI-likelihood: 12%).** Our analysis found the natural
> variation in sentence length, vocabulary, and phrasing that is typical of
> human writing. This is an automated assessment, not a guarantee.

### Variant C — uncertain (`0.40 ≤ score < 0.70`)

Template:
```
Inconclusive (AI-likelihood: {pct}%).
Our two detectors disagreed or found mixed evidence, so we cannot confidently
say whether this text was written by a human or by AI.
We are NOT flagging this as AI-generated. If a decision depends on this result,
please treat it as undetermined.
```
Rendered at `score = 0.58`:
> **Inconclusive (AI-likelihood: 58%).** Our two detectors disagreed or found
> mixed evidence, so we cannot confidently say whether this text was written by
> a human or by AI. We are NOT flagging this as AI-generated. If a decision
> depends on this result, please treat it as undetermined.

---

## 4. Appeals workflow

- **Who can appeal:** the creator who submitted the text — in practice, anyone
  holding the `submission_id` returned by `/submit`. *(Limitation: this prototype
  has no authentication, so possession of the id is the only credential. Noted as
  a known gap for a production version.)*
- **What they provide:** `submission_id` (required), `reason` (required free
  text — why they believe the verdict is wrong), and an optional
  `claimed_author` note.
- **What the system does on receipt:**
  1. Validate `submission_id` exists → `404` if not.
  2. Create an appeal record: `appeal_id`, `submission_id`, `reason`,
     `claimed_author`, `created_at`, `status = "open"`.
  3. Update the original submission's status: `active → under_review`.
  4. Write **both** changes to the audit log (the appeal record + the status change).
  5. Return `{ appeal_id, submission_id, status: "under_review", timestamp }`.
- **What a human reviewer sees (appeal queue):** `GET /appeals?status=open`
  returns the queue. Each entry shows: `appeal_id`, `submission_id`, the original
  **verdict + score + per-signal breakdown (s1, s2, stylometric features)**, a
  **text excerpt**, the appellant's **reason**, and timestamps. The reviewer can
  resolve an appeal by moving its status to `resolved_upheld` (detection stands)
  or `resolved_overturned` (verdict reversed); the submission status follows.
  *(The resolution UI is out of scope for the prototype, but the data model —
  the `status` field with these values — fully supports it.)*

---

## 5. Anticipated edge cases

Specific failure scenarios this system will handle poorly, and how the design
softens each.

1. **Repetitive poem / song lyrics with simple vocabulary and short, even lines.**
   Short uniform lines drive burstiness *down* and lexical diversity *down*, so
   the stylometric signal (`s2`) reads strongly AI-like — but it's human creative
   writing. *Softening:* the LLM signal often recognizes creative/poetic intent
   and disagrees, which the agreement factor converts into an *uncertain* verdict
   rather than a false AI flag; very short lines also reduce `length_factor`.

2. **Non-native English / ESL writing.** Simpler, more uniform sentence structure
   and a smaller vocabulary push *both* signals toward AI-like. This is a real
   fairness risk — the system could systematically misjudge a vulnerable group.
   *Softening:* the conservative 0.70 AI threshold, the wide uncertain band, the
   hedged label, and the appeal path. Documented explicitly as a known bias, not
   solved by the algorithm.

Additional cases the design also catches (handled by the same machinery):

3. **Very short text** (a tweet, one or two sentences) → unreliable statistics →
   `length_factor` damps the score toward uncertain.
4. **Technical / legal / academic boilerplate** → inherently uniform regardless
   of author → stylometric false-AI; mitigated by requiring signal agreement.
5. **Human-edited AI ("hybrid") text** → genuinely ambiguous authorship → the
   system *correctly* reports uncertain rather than guessing.

---

## Architecture

ASCII diagrams below are the **canonical reference** carried into Milestones 3–5
and pasted into AI tools when generating code. Arrows are labeled with what
passes between components.

**Submission flow**

```
                         (over quota → 429)
            ┌─────────────────────────────────────────────┐
 raw text   │                                             │
 [Client] ──┴─► [Rate limiter] ──within quota──► [Input validator]
                                                     │  │
                              (invalid → 400) ◄───────┘  │ valid text
                                                         ▼
                                  ┌──────────────────────┴───────────────────┐
                                  │                                          │
                         valid text│                                 valid text│
                                  ▼                                          ▼
                    [Signal 1: LLM classifier (Groq)]      [Signal 2: stylometric analyzer]
                                  │                                          │
                       s1 + rationale│                          s2 + feature values│
                                  └──────────────┬───────────────────────────┘
                                                 ▼
                                     [Confidence scorer]
                                                 │  calibrated score + verdict band
                                                 ▼
                                  [Transparency label generator]
                                                 │  verdict + label + breakdown
                                                 ▼
                                       [Audit log  (SQLite/JSON)]
                                                 │  submission_id, score, verdict, label
                                                 ▼
                                            [Client]
```

**Appeal flow**

```
                         (over quota → 429)
 submission_id + reason  ┌───────────────────────────────┐
 [Client] ───────────────┴─► [Rate limiter] ──ok──► [Appeal handler]
                                                        │  lookup submission_id
                                                        ▼
                                              [Audit log (SQLite/JSON)]
                                                        │  (not found → 404 → Client)
                                                        │  set status: active → under_review
                                                        │  append appeal record (reason, ts)
                                                        ▼
                                                   [Client]
                                              appeal_id + status: under_review
```

**Narrative:** A submission passes the rate limiter and validator, then fans out
to the two independent signals (LLM `s1`, stylometric `s2`), which the confidence
scorer blends and damps into one calibrated score and verdict band; the label
generator renders the matching transparency label, everything is written to the
audit log, and the result returns to the client. An appeal looks the submission
up in the audit log, flips its status to `under_review`, appends a linked appeal
record, and confirms back to the appellant.

---

## API surface

All endpoints sit behind the rate limiter; all bodies/responses are JSON.

### `POST /submit`
- **Request:** `{ "text": "<text to analyze>" }`
- **Response 200:**
  ```json
  {
    "submission_id": "uuid",
    "verdict": "likely human | uncertain | likely AI",
    "score": 0.0,
    "signals": {
      "llm": { "s1": 0.0, "rationale": "..." },
      "stylometric": {
        "s2": 0.0,
        "features": {
          "burstiness_cv": 0.0,
          "mattr": 0.0,
          "punctuation_variety": 0,
          "avg_sentence_length": 0.0
        }
      }
    },
    "label": "rendered transparency label text",
    "status": "active",
    "timestamp": "ISO-8601"
  }
  ```
- **Errors:** `400` invalid/empty/oversized · `429` rate limited · `502/503` Groq failure.

### `POST /appeal`
- **Request:** `{ "submission_id": "uuid", "reason": "...", "claimed_author": "optional" }`
- **Response 200:** `{ "appeal_id": "uuid", "submission_id": "uuid", "status": "under_review", "timestamp": "ISO-8601" }`
- **Errors:** `400` missing fields · `404` unknown submission · `429` rate limited.

### `GET /submissions/<submission_id>`
- **Response 200:** the audit record (score, verdict, signals, label, status, appeals[]).
- **Errors:** `404` unknown id.

### `GET /appeals?status=open`
- **Response 200:** the reviewer queue (see §4). Supports `status` filter.

### `GET /health`
- **Response 200:** `{ "status": "ok" }`

---

## AI Tool Plan

How each implementation milestone will be generated with AI tools: which spec
sections to paste in, what to ask for, and how to verify.

### M3 — submission endpoint + first signal
- **Spec sections to provide:** §1 Detection signals (LLM portion), the
  Architecture diagram, and `POST /submit` from the API surface.
- **Ask the AI to generate:** a Flask app skeleton (`app.py`, env loading via
  `python-dotenv`, Flask-Limiter wired up) plus a `detect_llm(text) -> {s1, rationale}`
  function that calls Groq `llama-3.3-70b-versatile` and parses the JSON output.
- **How to verify:** call `detect_llm` directly on a handful of inputs (a known
  ChatGPT paragraph, a personal hand-written paragraph) **before** wiring it into
  the endpoint; confirm `s1` is high for AI text and low for human text and that
  malformed model output is handled gracefully.

### M4 — second signal + confidence scoring
- **Spec sections to provide:** §1 Detection signals (stylometric + combination),
  §2 Uncertainty representation (incl. the worked-examples table), and the diagram.
- **Ask the AI to generate:** a `detect_stylometric(text) -> {s2, features}`
  function implementing the four-feature table, and a `combine(s1, s2, word_count)
  -> {score, verdict}` function implementing Stages A/B and the thresholds.
- **How to verify:** run the four worked examples from §2 and confirm the
  computed `score`/verdict match the table; confirm scores vary *meaningfully*
  between clearly-AI and clearly-human samples, and that conflicting/short inputs
  land in *uncertain*. Tune the provisional anchors here.

### M5 — production layer (labels + appeals)
- **Spec sections to provide:** §3 Transparency label variants, §4 Appeals
  workflow, and the diagram.
- **Ask the AI to generate:** a `make_label(score) -> str` function returning the
  correct variant (A/B/C) with `{pct}` filled, the audit-log store (SQLite or
  JSON), the `POST /appeal` endpoint, and `GET /appeals`/`GET /submissions/<id>`.
- **How to verify:** confirm all three label variants are reachable by feeding
  scores of 0.12, 0.58, and 0.86; confirm an appeal updates the submission status
  `active → under_review`, writes an audit record, and appears in the reviewer
  queue.

---

## Milestone 2 checkpoint — status

- [x] Addresses all five questions with specific, implementation-ready answers (§1–§5).
- [x] Three label variants written out verbatim (§3): high-confidence AI, high-confidence human, uncertain.
- [x] Confidence scoring produces different labels at different score ranges — not a binary flip at 0.5 (§2 thresholds 0.40 / 0.70 + worked examples).
- [x] `## Architecture` section includes the Milestone 1 diagram (ASCII) + narrative.
- [x] `## AI Tool Plan` covers M3, M4, M5 with sections, requests, and verification steps.

*Stretch features:* update this document before starting any stretch work.
