"""Transparency label generation for Provenance Guard.

The label text a user sees is derived from the calibrated confidence score. The
three variants (and their exact wording) are defined in planning.md section 3.
The label changes with the score — it is never the same text regardless of score.
"""

from detection import AI_MIN, HUMAN_MAX

LABEL_AI = (
    "Likely AI-generated (AI-likelihood: {pct}%). "
    "Both our writing-style analysis and our language-model reviewer found "
    "patterns strongly associated with AI-generated text — such as unusually "
    "uniform sentence structure and generic phrasing. This is an automated "
    "assessment and can be wrong. If you wrote this yourself, you can appeal "
    "this result."
)

LABEL_HUMAN = (
    "Likely human-written (AI-likelihood: {pct}%). "
    "Our analysis found the natural variation in sentence length, vocabulary, "
    "and phrasing that is typical of human writing. This is an automated "
    "assessment, not a guarantee."
)

LABEL_UNCERTAIN = (
    "Inconclusive (AI-likelihood: {pct}%). "
    "Our two detectors disagreed or found mixed evidence, so we cannot "
    "confidently say whether this text was written by a human or by AI. We are "
    "NOT flagging this as AI-generated. If a decision depends on this result, "
    "please treat it as undetermined."
)


def make_label(score):
    """Return the transparency label text for a 0-1 confidence score."""
    pct = round(score * 100)
    if score >= AI_MIN:
        return LABEL_AI.format(pct=pct)
    if score < HUMAN_MAX:
        return LABEL_HUMAN.format(pct=pct)
    return LABEL_UNCERTAIN.format(pct=pct)


if __name__ == "__main__":
    for s in (0.12, 0.58, 0.86):
        print(f"score={s}:\n  {make_label(s)}\n")
