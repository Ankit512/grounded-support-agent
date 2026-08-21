"""retriever.py — deterministic BM25 retrieval over the markdown knowledge base,
plus a fingerprint of the exact KB that produced a result.

Standard library only. There is no embedding model and no network in the trust
path: given the same KB and the same query, this returns the same ranking every
time, and every ranking can be audited by hand. The honest cost of that choice is
weaker recall on heavy paraphrases (see the retrieval trade-off note in README);
it biases toward escalation, which is the safe failure for a support agent.

A "passage" is one section of a KB file (delimited by markdown headings). A
RESOLVE cites a passage by its stable id (``<file>#<slug>``), so every grounded
answer names exactly where it came from.
"""

import hashlib
import math
import os
import re

# --- tokenization ---------------------------------------------------------
# Lowercase alphanumeric runs. Kept deliberately simple and inspectable: the
# whole point is that a human can reproduce the token set by eye.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A small, fixed stopword list. These carry no topic signal, so counting them in
# term coverage would let an out-of-scope question borrow credit from filler
# words. Frozen here (not learned) so the decision stays auditable.
STOPWORDS = frozenset("""
a an and are as at be been but by can cant do does doing done for from get gets
getting had has have how i id im in into is it its me my no not of on or our so t
that the their them then there these they this to up us was we were what when
where which who why will with would you your yours
""".split())


def _stem(token):
    """A tiny deterministic suffix trim so 'tickets'/'ticket' and
    'refunds'/'refund' match. Intentionally crude and rule-based — not a real
    stemmer — so its behavior is obvious from reading it."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def tokenize(text):
    """Return the list of content tokens (lowercased, stopwords removed,
    lightly stemmed). Order preserved; duplicates kept for term frequency."""
    out = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if raw in STOPWORDS or len(raw) < 2:
            continue
        out.append(_stem(raw))
    return out


# --- passages -------------------------------------------------------------
def _slug(heading):
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-") or "section"


class Passage:
    """One citeable section of the KB."""

    __slots__ = ("id", "topic", "heading", "text", "tokens", "token_set")

    def __init__(self, topic, heading, text):
        self.topic = topic
        self.heading = heading
        self.text = text.strip()
        self.id = "{}#{}".format(topic, _slug(heading))
        self.tokens = tokenize(self.text + " " + heading)
        self.token_set = frozenset(self.tokens)

    def __repr__(self):
        return "Passage({!r})".format(self.id)


def _split_passages(topic, raw):
    """Split one markdown file into passages on '## ' headings. Text before the
    first '## ' (the '# Title' block) is attached to the file as a whole."""
    passages = []
    heading = None
    buf = []

    def flush():
        if heading is not None and buf:
            body = "\n".join(buf).strip()
            if body:
                passages.append(Passage(topic, heading, body))

    for line in raw.splitlines():
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            buf = []
        elif line.startswith("# "):
            continue  # file title; not its own passage
        else:
            buf.append(line)
    flush()
    return passages


# --- retriever ------------------------------------------------------------
class Retriever:
    """BM25 over the KB passages. Deterministic and self-contained.

    Parameters ``k1`` and ``b`` are the standard BM25 knobs and are surfaced in
    provenance so a result can be reproduced exactly.
    """

    def __init__(self, kb_dir, k1=1.5, b=0.75):
        self.kb_dir = kb_dir
        self.k1 = k1
        self.b = b
        self.passages = []
        self._df = {}          # term -> number of passages containing it
        self._avgdl = 0.0
        self._fingerprint = None
        self._load()

    # -- loading & fingerprint --------------------------------------------
    def _load(self):
        files = sorted(
            f for f in os.listdir(self.kb_dir) if f.endswith(".md")
        )
        hasher = hashlib.sha256()
        total_len = 0
        for name in files:
            path = os.path.join(self.kb_dir, name)
            with open(path, "rb") as fh:
                raw_bytes = fh.read()
            # Fingerprint over (name, exact bytes) of every KB file, in sorted
            # order, so the hash pins the precise KB that produced a decision.
            hasher.update(name.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(raw_bytes)
            hasher.update(b"\0")
            topic = name[:-3]
            for passage in _split_passages(topic, raw_bytes.decode("utf-8")):
                self.passages.append(passage)
                total_len += len(passage.tokens)
                for term in passage.token_set:
                    self._df[term] = self._df.get(term, 0) + 1

        if not self.passages:
            raise ValueError("knowledge base is empty: {}".format(self.kb_dir))

        self._avgdl = total_len / len(self.passages)
        self._fingerprint = hasher.hexdigest()

    @property
    def fingerprint(self):
        """sha256 over the exact KB file bytes. Travels in every provenance
        block so an answer can be tied to the KB that produced it."""
        return self._fingerprint

    @property
    def num_passages(self):
        return len(self.passages)

    # -- scoring -----------------------------------------------------------
    def _idf(self, term):
        n = len(self.passages)
        df = self._df.get(term, 0)
        # BM25 idf with the standard +0.5 smoothing, floored at 0 so a term in
        # every passage cannot pull a score negative.
        return max(0.0, math.log((n - df + 0.5) / (df + 0.5) + 1.0))

    def _bm25(self, query_tokens, passage):
        dl = len(passage.tokens)
        if dl == 0:
            return 0.0
        tf = {}
        for t in passage.tokens:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for term in query_tokens:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = self._idf(term)
            denom = f + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
            score += idf * (f * (self.k1 + 1)) / denom
        return score

    def search(self, query, k=3):
        """Rank passages for ``query``. Returns a list of dicts (highest first):

            {"passage": Passage, "bm25": float, "coverage": float,
             "matched": [terms], "missing": [terms]}

        ``coverage`` is the fraction of the query's distinct content terms that
        appear in that passage — the honest "did we actually cover what they
        asked" signal that the resolver gates on.
        """
        q_tokens = tokenize(query)
        q_distinct = list(dict.fromkeys(q_tokens))  # order-preserving unique
        ranked = []
        for passage in self.passages:
            bm25 = self._bm25(q_tokens, passage)
            matched = [t for t in q_distinct if t in passage.token_set]
            missing = [t for t in q_distinct if t not in passage.token_set]
            coverage = (len(matched) / len(q_distinct)) if q_distinct else 0.0
            ranked.append({
                "passage": passage,
                "bm25": round(bm25, 4),
                "coverage": round(coverage, 4),
                "matched": matched,
                "missing": missing,
            })
        # Sort by BM25, then coverage, then id for a fully deterministic order.
        ranked.sort(key=lambda r: (r["bm25"], r["coverage"], r["passage"].id),
                    reverse=True)
        return ranked[:k]

    def provenance(self):
        """The retriever half of a provenance block (thresholds are added by the
        resolver, which owns them)."""
        return {
            "retriever": "bm25",
            "k1": self.k1,
            "b": self.b,
            "kb_dir": os.path.basename(os.path.normpath(self.kb_dir)),
            "kb_sha256": self._fingerprint,
            "kb_passages": len(self.passages),
        }
