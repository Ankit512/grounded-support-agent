"""The entailment guard that makes "the model adds nothing" testable.

The agent never needs a model: a RESOLVED answer is the cited passage text verbatim.
An optional LLM layer may reword that passage to sound conversational, but it is given
ONLY the cited passage and is allowed to add nothing to it. This module enforces that
claim deterministically, with no runtime model dependency.

The guard: every content word and every number in a candidate rephrase must be grounded
in the cited passage's tokens. If the rephrase introduces an unsupported term or an
unsupported number (a phone line, a different time window, a "yes" the passage never
said), the rephrase is REJECTED and the agent falls back to the raw cited answer.
Conservative by design: a rephrase that merely uses a synonym the passage lacks is also
rejected, because the safe move is to show the cited text.
"""
from __future__ import annotations
import re

from .retriever import tokenize

# Numbers must be grounded exactly: a rephrase may not invent "24 hours" when the passage
# said "60 minutes". We compare the digit strings verbatim.
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def check(rephrase: str, passage_text: str) -> dict:
    """Return a report on whether ``rephrase`` is entailed by ``passage_text``.

        {"accepted": bool,
         "text": str,                    # the rephrase if accepted, else the raw passage
         "unsupported_terms": [str],     # content words not found in the passage
         "unsupported_numbers": [str],   # numbers not found verbatim in the passage
         "reason": str}
    """
    passage_terms = set(tokenize(passage_text))
    passage_nums = set(_NUM.findall(passage_text))

    seen: set[str] = set()
    unsupported_terms: list[str] = []
    for t in tokenize(rephrase):
        if t not in passage_terms and t not in seen:
            unsupported_terms.append(t)
            seen.add(t)

    unsupported_numbers = [n for n in _NUM.findall(rephrase) if n not in passage_nums]

    accepted = not unsupported_terms and not unsupported_numbers
    return {
        "accepted": accepted,
        "text": rephrase if accepted else passage_text,
        "unsupported_terms": unsupported_terms,
        "unsupported_numbers": unsupported_numbers,
        "reason": ("rephrase is fully grounded in the cited passage" if accepted
                   else "rephrase introduced content not grounded in the cited passage; "
                        "falling back to the raw cited answer"),
    }


def apply(rephrase: str, passage_text: str) -> str:
    """Return the text to actually show the customer: the rephrase if it is fully
    grounded, otherwise the raw cited passage. Never returns ungrounded text."""
    return check(rephrase, passage_text)["text"]
