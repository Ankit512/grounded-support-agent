#!/usr/bin/env python3
"""ask.py — ask the Grounded Support Agent a question from the command line.

    python3 ask.py "how do I reset my password?"
    python3 ask.py --json "can I get a refund after 30 days?"

Standard library only. No model, no network, no pip install. The decision is
made by core/resolver.py; this file only formats it.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.resolver import Resolver  # noqa: E402

KB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb")


def _format_plain(d):
    lines = []
    q = d["question"]
    lines.append('Q: {}'.format(q))
    prov = d["provenance"]
    if d["outcome"] == "RESOLVE":
        cite = d["citation"]
        lines.append("")
        lines.append("RESOLVE  (confidence {}%)".format(d["confidence"]))
        lines.append("")
        lines.append(d["answer"])
        lines.append("")
        lines.append("Source : {} — “{}”".format(cite["topic"], cite["heading"]))
        lines.append("Cited  : {}".format(cite["passage_id"]))
    else:
        lines.append("")
        lines.append("ESCALATE  ({})".format(d["reason"]))
        lines.append("")
        lines.append(d.get("handoff", ""))
        if d["evidence"]:
            lines.append("")
            lines.append("Closest passages (for the human, no decision made):")
            for e in d["evidence"]:
                lines.append("  - {} — “{}”  (score {}, coverage {:.0%})".format(
                    e["topic"], e["heading"], e["score"], e["coverage"]))
    # Provenance travels with every answer.
    lines.append("")
    lines.append("Provenance:")
    lines.append("  KB sha256   : {}".format(prov["kb_sha256"]))
    lines.append("  retriever   : {} (k1={}, b={}), {} passages".format(
        prov["retriever"], prov["k1"], prov["b"], prov["kb_passages"]))
    th = prov["thresholds"]
    lines.append("  thresholds  : resolve_coverage>={} resolve_score>={} "
                 "relevance_floor>={}".format(
                     th["resolve_coverage"], th["resolve_score"],
                     th["relevance_floor"]))
    lines.append("  top score   : {}   coverage: {:.0%}".format(
        prov["score"], prov["coverage"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ask the Grounded Support Agent (resolve or escalate).")
    parser.add_argument("question", nargs="?", default="",
                        help="the customer question")
    parser.add_argument("--json", action="store_true",
                        help="emit the full decision as JSON")
    parser.add_argument("--kb", default=KB_DIR, help="knowledge base directory")
    args = parser.parse_args(argv)

    if not args.question.strip():
        parser.error("a question is required, e.g. ask.py \"how do I reset my password?\"")

    resolver = Resolver.from_kb(args.kb)
    decision = resolver.resolve_or_escalate(args.question)

    if args.json:
        print(json.dumps(decision, indent=2))
    else:
        print(_format_plain(decision))

    # Exit 0 for a resolve, 2 for an escalation — lets a script branch on it.
    return 0 if decision["outcome"] == "RESOLVE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
