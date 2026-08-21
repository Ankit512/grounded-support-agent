"""The decision engine: resolve what the knowledge base can prove, escalate the rest.

The principle, carried over from the itsoc log tool: the model never owns the verdict.
Here the *verdict* is whether we are allowed to answer at all. That call is made by
deterministic retrieval and coverage, not by a language model, so the agent cannot talk
itself into a confident wrong answer. A model may later phrase a RESOLVED answer, but only
from passages that already cleared the bar; it is never asked to decide, only to word.

Three outcomes, and only three:
  RESOLVE   the KB covers the question well enough to answer, with citations
  ESCALATE  the KB is partially relevant but not strong enough to answer safely
  ESCALATE  the KB does not cover this at all -> honest handoff, no guessing
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json

from .retriever import BM25, Passage, tokenize, load_passages, kb_fingerprint

# Thresholds are explicit and auditable, not hidden inside a prompt.
RESOLVE_COVERAGE = 0.60     # fraction of the question's content terms the top passage must cover
RESOLVE_MIN_SCORE = 1.0     # minimum absolute BM25 score, guards against spurious weak matches
WEAK_COVERAGE = 0.30        # below this, treat as "not covered" rather than "low confidence"


@dataclass
class Citation:
    passage_id: str
    doc: str
    heading: str
    score: float


@dataclass
class Decision:
    outcome: str                 # "resolved" | "escalated"
    reason: str                  # machine reason code
    confidence: float            # 0..1, term coverage of the winning passage
    answer: str | None           # grounded answer text, or None when escalated
    citations: list[Citation]
    handoff_note: str | None     # context handed to the human when escalated
    provenance: dict


def _coverage(query: str, passage: Passage) -> float:
    q = set(tokenize(query))
    if not q:
        return 0.0
    have = set(passage.tokens)
    return len(q & have) / len(q)


class Resolver:
    def __init__(self, kb_dir: str):
        self.passages = load_passages(kb_dir)
        self.bm25 = BM25(self.passages)
        self.kb_hash = kb_fingerprint(self.passages)

    def resolve(self, question: str, top_k: int = 3) -> Decision:
        hits = self.bm25.search(question, top_k=top_k)
        cites = [Citation(p.passage_id, p.doc, p.heading, round(s, 3)) for p, s in hits]
        prov = {
            "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kb_sha256_16": self.kb_hash,
            "retriever": "bm25",
            "thresholds": {
                "resolve_coverage": RESOLVE_COVERAGE,
                "resolve_min_score": RESOLVE_MIN_SCORE,
                "weak_coverage": WEAK_COVERAGE,
            },
            "question_sha256_16": hashlib.sha256(question.encode()).hexdigest()[:16],
        }

        # Nothing retrieved at all -> honest "not covered".
        if not hits:
            return Decision(
                outcome="escalated", reason="no_match", confidence=0.0, answer=None,
                citations=[], provenance=prov,
                handoff_note="No knowledge-base passage matched this question. "
                             "Routing to a human; do not answer from the model alone.",
            )

        top_p, top_s = hits[0]
        cov = _coverage(question, top_p)
        prov["top_score"] = round(top_s, 3)
        prov["top_coverage"] = round(cov, 3)

        # Strong coverage AND a real score -> we are allowed to answer, with the source.
        if cov >= RESOLVE_COVERAGE and top_s >= RESOLVE_MIN_SCORE:
            return Decision(
                outcome="resolved", reason="grounded", confidence=round(cov, 3),
                answer=top_p.text, citations=cites, handoff_note=None, provenance=prov,
            )

        # Some relevance but not enough to be safe -> escalate with context, never guess.
        if cov >= WEAK_COVERAGE:
            return Decision(
                outcome="escalated", reason="low_confidence", confidence=round(cov, 3),
                answer=None, citations=cites, provenance=prov,
                handoff_note=f"Partial match (coverage {cov:.0%}) below the resolve bar. "
                             f"Closest source: {top_p.passage_id}. Handing to a human with "
                             f"the top passages attached rather than risk a wrong answer.",
            )

        # Retrieved noise, not real coverage -> treat as not covered.
        return Decision(
            outcome="escalated", reason="insufficient_coverage", confidence=round(cov, 3),
            answer=None, citations=cites, provenance=prov,
            handoff_note="The knowledge base does not meaningfully cover this question. "
                         "Routing to a human; the model is not permitted to answer.",
        )


def decision_to_json(d: Decision) -> str:
    out = asdict(d)
    return json.dumps(out, indent=2)
