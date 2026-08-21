"""resolver.py — the resolve-or-escalate decision engine.

This module, not a model, decides whether the agent is allowed to answer. The
decision is a function of two deterministic signals from the retriever:

  * ``coverage`` — the fraction of the customer's distinct content words that
    actually appear in the top passage. This is the honesty gate: if the words
    they asked about are not in the passage, we have not covered their question
    and we do not get to claim we did.
  * ``score``    — the BM25 relevance of the top passage. A floor on it separates
    "partly relevant, escalate for a human" from "nothing here, no match".

The thresholds are explicit constants below and travel in every provenance
block, so any decision can be re-derived and audited by hand. There is no prompt
asking a model to be careful.

Outcomes (exactly three):
  RESOLVE                  coverage >= RESOLVE_COVERAGE and score >= RESOLVE_SCORE
  ESCALATE low_confidence  some relevance (score >= RELEVANCE_FLOOR) but the bar
                           was not cleared
  ESCALATE no_match        not even relevant

Guarantees this enforces (and their honest limits):
  * No RESOLVE without a citation — a resolve always names its source passage.
  * An out-of-scope question (its content words are absent from every passage)
    cannot clear the coverage gate, so it can never RESOLVE. This is the tested
    invariant.
  * These guarantee the agent cannot give an *ungrounded* answer and cannot
    *resolve an out-of-scope* question. They do NOT claim it can never be wrong:
    a passage that is cited but mis-ranked could still ground a cited-but-wrong
    resolve. Grounding is guaranteed; perfect ranking is not.
"""

from .retriever import Retriever

# --- thresholds (explicit and auditable) ----------------------------------
# Raising RESOLVE_COVERAGE makes the agent stricter (more escalation, safer);
# lowering it resolves more but risks thin grounding. It is set as high as the
# KB's citations support, because the honesty guarantee is what lets us push it
# up without risking a confident wrong answer.
RESOLVE_COVERAGE = 0.5     # >= half the customer's content words in the passage
RESOLVE_SCORE = 1.0        # BM25 floor for a resolve
RELEVANCE_FLOOR = 0.4      # below this the KB simply does not cover the question

# Outcome constants
RESOLVE = "RESOLVE"
ESCALATE = "ESCALATE"

# Escalation reasons
LOW_CONFIDENCE = "low_confidence"
NO_MATCH = "no_match"
EMPTY_QUESTION = "empty_question"


def thresholds():
    """The threshold set, as a plain dict for provenance."""
    return {
        "resolve_coverage": RESOLVE_COVERAGE,
        "resolve_score": RESOLVE_SCORE,
        "relevance_floor": RELEVANCE_FLOOR,
    }


def _snippet(text, limit=280):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class Resolver:
    """Wraps a :class:`Retriever` and turns a question into a decision.

    The resolver holds no state beyond the retriever, so the same question over
    the same KB always yields the same decision — the property the eval checks.
    """

    def __init__(self, retriever):
        self.retriever = retriever

    @classmethod
    def from_kb(cls, kb_dir):
        return cls(Retriever(kb_dir))

    def _provenance(self, top):
        prov = self.retriever.provenance()
        prov["thresholds"] = thresholds()
        if top is not None:
            prov["score"] = top["bm25"]
            prov["coverage"] = top["coverage"]
            prov["matched_terms"] = top["matched"]
            prov["missing_terms"] = top["missing"]
        else:
            prov["score"] = 0.0
            prov["coverage"] = 0.0
            prov["matched_terms"] = []
            prov["missing_terms"] = []
        return prov

    def resolve_or_escalate(self, question, k=3):
        """Return a decision dict for ``question``.

        Shape (stable — the CLI, eval and MCP layer all read it):

            {
              "question": str,
              "outcome": "RESOLVE" | "ESCALATE",
              "reason": None | "low_confidence" | "no_match" | "empty_question",
              "confidence": int,          # 0-100, = coverage of the top passage
              "answer": str | None,       # only on RESOLVE, verbatim cited text
              "citation": {...} | None,   # only on RESOLVE
              "evidence": [ {...}, ... ], # ranked passages (for a human)
              "provenance": {...}         # KB hash, retriever, thresholds, score…
            }
        """
        question = (question or "").strip()
        ranked = self.retriever.search(question, k=k) if question else []
        top = ranked[0] if ranked else None

        evidence = [{
            "passage_id": r["passage"].id,
            "topic": r["passage"].topic,
            "heading": r["passage"].heading,
            "score": r["bm25"],
            "coverage": r["coverage"],
            "matched_terms": r["matched"],
            "snippet": _snippet(r["passage"].text),
        } for r in ranked]

        base = {
            "question": question,
            "evidence": evidence,
            "provenance": self._provenance(top),
        }

        # --- empty question -----------------------------------------------
        if not question or top is None:
            base.update({
                "outcome": ESCALATE,
                "reason": EMPTY_QUESTION,
                "confidence": 0,
                "answer": None,
                "citation": None,
                "handoff": "No question to answer. Escalating to a human.",
            })
            return base

        score = top["bm25"]
        coverage = top["coverage"]
        confidence = int(round(coverage * 100))

        # --- RESOLVE ------------------------------------------------------
        if coverage >= RESOLVE_COVERAGE and score >= RESOLVE_SCORE:
            passage = top["passage"]
            base.update({
                "outcome": RESOLVE,
                "reason": None,
                "confidence": confidence,
                # The answer is the cited passage text, verbatim. The model (if
                # any) may only reword this; it may not add to it.
                "answer": passage.text,
                "citation": {
                    "passage_id": passage.id,
                    "topic": passage.topic,
                    "heading": passage.heading,
                    "kb_sha256": self.retriever.fingerprint,
                },
            })
            return base

        # --- ESCALATE -----------------------------------------------------
        if score >= RELEVANCE_FLOOR:
            reason = LOW_CONFIDENCE
            handoff = (
                "The knowledge base is partly relevant but does not clearly "
                "cover this. Handing off to a human with the closest passages "
                "attached.")
        else:
            reason = NO_MATCH
            handoff = (
                "The knowledge base does not cover this question. Handing off "
                "to a human; the agent is not permitted to answer.")
        base.update({
            "outcome": ESCALATE,
            "reason": reason,
            "confidence": confidence,
            "answer": None,
            "citation": None,
            "handoff": handoff,
        })
        return base
