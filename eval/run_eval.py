#!/usr/bin/env python3
"""Eval harness. The headline property is not accuracy, it is honesty under ignorance:

    the agent must NEVER resolve a question it cannot ground -- above all, never resolve
    an out-of-scope one.

So hallucinations are a HARD-ZERO invariant: if the count is non-zero, this process
exits non-zero. A hallucination is any of:
  * an escalate-expected question that RESOLVED (out-of-scope / unsafe / a prompt
    injection that flipped a resolve);
  * a RESOLVE with no citation (ungrounded -- should be structurally impossible);
  * a RESOLVE whose top citation does not match the expected source, when the case
    pins one (a confident wrong answer).

Paraphrase recall is reported SEPARATELY from the plain resolution rate: a paraphrase we
escalate is a recall miss, not a lie -- the honest cost of a deterministic stdlib
retriever, which biases toward the safe failure (escalation), never toward a wrong answer.

The build passes only if: hallucinations == 0, every plain/nuanced case resolves to the
right source, and every escalate-expected case escalates. Paraphrase recall is
informational and never fails the build.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.resolver import Resolver

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")
QS = os.path.join(ROOT, "eval", "questions.jsonl")

ANSWERABLE_BUCKETS = {"plain", "nuanced"}
RECALL_BUCKETS = {"paraphrase"}


def classify(row, d):
    """Return (top_cite, cite_ok, hallucination, why) for one decision."""
    resolved = d.outcome == "resolved"
    top_cite = ""
    if resolved and d.citations:
        top_cite = d.citations[0].passage_id
    cite_ok = True
    if resolved and row.get("expect_cite"):
        cite_ok = row["expect_cite"] in top_cite

    hallucination, why = False, ""
    if row["expect"] == "escalate" and resolved:
        hallucination = True
        why = "resolved a question that must escalate ({})".format(row["bucket"])
    elif resolved and not top_cite:
        hallucination = True
        why = "resolved with no citation (ungrounded)"
    elif resolved and not cite_ok:
        hallucination = True
        why = "resolved with wrong citation: got {}, expected ~{}".format(
            top_cite or "(none)", row["expect_cite"])
    return top_cite, cite_ok, hallucination, why


def rate(num, den):
    pct = (100.0 * num / den) if den else 0.0
    return "{}/{} = {:.0f}%".format(num, den, pct)


def main():
    r = Resolver(KB)
    rows = [json.loads(l) for l in open(QS) if l.strip()]

    answerable = [x for x in rows if x["bucket"] in ANSWERABLE_BUCKETS]
    recall = [x for x in rows if x["bucket"] in RECALL_BUCKETS]
    escalate = [x for x in rows if x["expect"] == "escalate"]

    ans_ok = recall_ok = esc_ok = 0
    hallucinations = []

    print("KB sha256: {}".format(r.kb_hash))
    print("-" * 72)
    print("{:<12}{:<14}{:<10}{:<11}{}".format("expect", "bucket", "got", "conf", "ok"))
    print("-" * 72)
    for x in rows:
        d = r.resolve(x["q"])
        top_cite, cite_ok, hallu, why = classify(x, d)
        resolved = d.outcome == "resolved"

        if x["bucket"] in ANSWERABLE_BUCKETS and resolved and cite_ok:
            ans_ok += 1
        if x["bucket"] in RECALL_BUCKETS and resolved and cite_ok:
            recall_ok += 1
        if x["expect"] == "escalate" and not resolved:
            esc_ok += 1
        if hallu:
            hallucinations.append((x, why))

        correct = (d.outcome == x["expect"] + "d") and cite_ok
        mark = "HALLU" if hallu else ("PASS" if correct else "miss")
        print("{:<12}{:<14}{:<10}{:<11}{}".format(
            x["expect"], x["bucket"], d.outcome, "{:.0%}".format(d.confidence), mark))
        if why:
            print("    ^ {}".format(why))
    print("-" * 72)

    print("Resolution rate on answerable questions : {}".format(rate(ans_ok, len(answerable))))
    print("Paraphrase recall (reported separately) : {}".format(rate(recall_ok, len(recall))))
    print("Correct handoff on out-of-scope/unsafe  : {}".format(rate(esc_ok, len(escalate))))
    print("Confident wrong answers (hallucinations): {:<4}<-- must be 0".format(len(hallucinations)))

    build_ok = (len(hallucinations) == 0
                and ans_ok == len(answerable)
                and esc_ok == len(escalate))
    print("\nRESULT:", "PASS" if build_ok else "FAIL")
    if not build_ok:
        if hallucinations:
            print("\nHallucinations (this MUST be empty):")
            for x, why in hallucinations:
                print("  - [{}] {}".format(x["bucket"], why))
        if ans_ok != len(answerable):
            print("\nAnswerable questions that did not resolve to the right source: "
                  "{} of {}".format(len(answerable) - ans_ok, len(answerable)))
        if esc_ok != len(escalate):
            print("\nEscalate-expected questions that resolved: "
                  "{} of {}".format(len(escalate) - esc_ok, len(escalate)))

    # Hard-zero invariant: any hallucination (or a broken headline metric) => non-zero exit.
    return 0 if build_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
