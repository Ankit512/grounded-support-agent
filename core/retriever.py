"""Deterministic retrieval over the support knowledge base.

Standard library only. The retriever, not a model, decides which knowledge-base
passages are relevant and how strongly. Every downstream decision is traceable to
these scores, so the answer path can be audited without asking a model to be honest.
"""
from __future__ import annotations
import math
import os
import re
import hashlib
from dataclasses import dataclass, field

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "to", "of", "in", "on",
    "for", "and", "or", "i", "my", "me", "you", "your", "it", "this", "that", "with",
    "how", "do", "does", "did", "can", "cant", "cannot", "will", "would", "should",
    "what", "when", "where", "why", "which", "who", "if", "as", "at", "by", "from",
    "get", "got", "have", "has", "had", "not", "no", "so", "up", "out", "we", "they",
    "there", "here", "about", "into", "than", "then", "them",
}

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


@dataclass
class Passage:
    doc: str          # source filename, e.g. "refunds.md"
    heading: str      # nearest markdown heading, e.g. "Refund window"
    text: str         # the passage body
    passage_id: str   # stable id "doc#heading-slug"
    tokens: list[str] = field(default_factory=list)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def load_passages(kb_dir: str) -> list[Passage]:
    """Split each markdown doc into passages at '## ' section boundaries."""
    passages: list[Passage] = []
    for fn in sorted(os.listdir(kb_dir)):
        if not fn.endswith(".md"):
            continue
        raw = open(os.path.join(kb_dir, fn), encoding="utf-8").read()
        # split on level-2 headings, keep the heading with its body
        parts = re.split(r"\n(?=##\s)", raw)
        for part in parts:
            lines = part.strip().splitlines()
            if not lines:
                continue
            heading = "intro"
            body_lines = lines
            if lines[0].startswith("##"):
                heading = lines[0].lstrip("#").strip()
                body_lines = lines[1:]
            elif lines[0].startswith("#"):
                heading = lines[0].lstrip("#").strip()
                body_lines = lines[1:]
            text = " ".join(l.strip() for l in body_lines if l.strip())
            if not text:
                continue
            pid = f"{fn}#{_slug(heading)}"
            passages.append(Passage(doc=fn, heading=heading, text=text,
                                    passage_id=pid, tokens=tokenize(heading + " " + text)))
    return passages


class BM25:
    """Textbook BM25 over the KB passages. No external dependencies."""

    def __init__(self, passages: list[Passage], k1: float = 1.5, b: float = 0.75):
        self.passages = passages
        self.k1, self.b = k1, b
        self.N = len(passages)
        self.avgdl = sum(len(p.tokens) for p in passages) / max(self.N, 1)
        self.df: dict[str, int] = {}
        for p in passages:
            for term in set(p.tokens):
                self.df[term] = self.df.get(term, 0) + 1
        self.idf = {
            t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in self.df.items()
        }

    def score(self, query_tokens: list[str], p: Passage) -> float:
        if not p.tokens:
            return 0.0
        freq: dict[str, int] = {}
        for t in p.tokens:
            freq[t] = freq.get(t, 0) + 1
        dl = len(p.tokens)
        s = 0.0
        for t in query_tokens:
            if t not in freq:
                continue
            idf = self.idf.get(t, 0.0)
            tf = freq[t]
            denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            s += idf * (tf * (self.k1 + 1)) / denom
        return s

    def search(self, query: str, top_k: int = 3) -> list[tuple[Passage, float]]:
        q = tokenize(query)
        scored = [(p, self.score(q, p)) for p in self.passages]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [(p, s) for p, s in scored[:top_k] if s > 0]


def kb_fingerprint(passages: list[Passage]) -> str:
    """Stable hash of the KB contents, carried in provenance so an answer can be
    tied to the exact knowledge base that produced it."""
    h = hashlib.sha256()
    for p in sorted(passages, key=lambda x: x.passage_id):
        h.update(p.passage_id.encode())
        h.update(p.text.encode())
    return h.hexdigest()[:16]


def default_kb_dir() -> str:
    """Filesystem path to the bundled knowledge base.

    Resolves the KB whether the code runs from a repo checkout or from a
    pip/uvx-installed wheel, so `grounded-support-agent` works standalone with no
    checkout. Order:

      1. The `kb` package shipped in the wheel (via importlib.resources) — this is
         how an installed copy finds its own KB.
      2. Fallback to `../kb` relative to this file — the in-repo layout.

    Callers may still pass an explicit directory to Retriever/Resolver (and the
    env var GSA_KB_DIR overrides at the server layer); this is only the default.
    """
    try:
        from importlib import resources
        path = os.fspath(resources.files("kb"))
        if os.path.isdir(path):
            return path
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "kb")
