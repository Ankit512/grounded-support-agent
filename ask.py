#!/usr/bin/env python3
"""Ask the grounded support agent a question.

    python3 ask.py "how do I reset my password?"
    python3 ask.py --json "can I get a refund after 30 days?"

Resolves only what the knowledge base can prove, with citations; otherwise hands off
to a human honestly. No answer is ever produced that is not grounded in a cited passage.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.resolver import Resolver, decision_to_json

KB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb")

def main(argv):
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if not argv:
        print("usage: python3 ask.py [--json] \"your question\"")
        return 2
    question = " ".join(argv)
    d = Resolver(KB).resolve(question)
    if as_json:
        print(decision_to_json(d))
        return 0
    print(f"Q: {question}\n")
    if d.outcome == "resolved":
        print(f"[RESOLVED  confidence {d.confidence:.0%}]")
        print(d.answer)
        srcs = ", ".join(c.passage_id for c in d.citations if c.score > 0)
        print(f"\nSource: {srcs}")
    else:
        print(f"[ESCALATED  {d.reason}  confidence {d.confidence:.0%}]")
        print(d.handoff_note)
        if d.citations and any(c.score > 0 for c in d.citations):
            near = ", ".join(c.passage_id for c in d.citations if c.score > 0)
            print(f"\nClosest sources for the human: {near}")
    print(f"\nprovenance: kb {d.provenance['kb_sha256_16']} "
          f"| top_score {d.provenance.get('top_score', 0)} "
          f"| coverage {d.provenance.get('top_coverage', 0)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
