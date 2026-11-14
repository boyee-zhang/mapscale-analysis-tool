"""The fan-out planner.

The case that matters is the real one at the bottom: the 191 headings from 2026-11-12, whose
true distribution is reproduced here. The planner has to say "one agent" for it, because the
measured alternative cost 357,877 tokens to reach the same 8 answers.
"""
import pytest

from tools.fanout import (DEFAULT_BUDGET_TOKENS, FIXED_COST_TOKENS, dedupe,
                            estimate_tokens, plan)


# ── dedupe ───────────────────────────────────────────────────────────────────

def test_dedupe_keeps_input_order_and_groups_every_index():
    units = ["b", "a", "b", "c", "a", "b"]
    reps, groups = dedupe(units)
    assert reps == ["b", "a", "c"]
    assert [len(v) for v in groups.values()] == [3, 2, 1]
    # every original index is accounted for exactly once
    seen = sorted(i for idxs in groups.values() for i in idxs)
    assert seen == list(range(len(units)))


def test_dedupe_uses_the_key_not_the_whole_unit():
    units = [{"t": "x", "doc": 1}, {"t": "x", "doc": 2}, {"t": "y", "doc": 3}]
    reps, groups = dedupe(units, key=lambda u: u["t"])
    assert len(reps) == 2
    assert reps[0]["doc"] == 1          # the first of each group represents it


def test_dedupe_of_nothing():
    reps, groups = dedupe([])
    assert reps == [] and groups == {}


# ── expand ───────────────────────────────────────────────────────────────────

def test_expand_puts_one_decision_back_on_every_unit():
    units = ["a", "b", "a", "a", "c"]
    p = plan(units)
    labels = {k: f"L{i}" for i, k in enumerate(p.groups)}
    assert p.expand(labels) == ["L0", "L1", "L0", "L0", "L2"]


def test_expand_refuses_a_partial_answer():
    # A judgement that silently covered only some keys would expand to a result full of
    # None that reads like a complete one.
    p = plan(["a", "b", "c"])
    partial = {k: "L" for k in list(p.groups)[:2]}
    with pytest.raises(KeyError, match="sent but not judged"):
        p.expand(partial)


def test_expand_refuses_an_answer_to_something_never_sent():
    p = plan(["a"])
    labels = {k: "L" for k in p.groups}
    labels["not-a-key-we-sent"] = "L"
    with pytest.raises(KeyError, match="never sent"):
        p.expand(labels)


# ── the gate ─────────────────────────────────────────────────────────────────

def test_small_payload_is_one_agent():
    p = plan([f"unit {i}" for i in range(50)])
    assert len(p.shards) == 1
    assert not p.fans_out
    assert "inside the" in p.why


def test_a_payload_over_budget_is_split():
    # 40 DISTINCT units x ~4,000 tokens each = ~160k, over a 100k budget. They have to differ:
    # 40 copies of one string is a dedupe test, not a budget test, and the first version of
    # this case was exactly that -- it collapsed to one unit and never reached the gate.
    big = [f"{i:04d}" + "x" * 16_000 for i in range(40)]
    p = plan(big)
    assert p.fans_out
    assert len(p.shards) == 2
    assert sum(len(s) for s in p.shards) == p.n_distinct
    assert "over the" in p.why


def test_the_budget_is_the_knob():
    units = [f"unit {i}" * 20 for i in range(100)]
    assert len(plan(units).shards) == 1
    assert len(plan(units, budget_tokens=200).shards) > 1


def test_nothing_to_judge():
    p = plan([])
    assert p.shards == [] and p.n_units == 0 and "nothing" in p.why


def test_estimate_is_on_the_rendered_text():
    assert estimate_tokens(["a" * 400]) == 100
    assert estimate_tokens([{"t": "a" * 400}], render=lambda u: u["t"]) == 100


# ── the real case ────────────────────────────────────────────────────────────

# The true distribution of the 191 tussenkop headings judged on 2026-11-12.
REAL = ([("Niet mee eens?", 73),
         ("Voorlopige voorziening", 73),
         ("Besluit:", 39),
         ("Met inachtneming van:", 2),
         ("Meer informatie", 1),
         ("Beoordeling en Besluit", 1),
         ("Bezwaar", 1),
         ("Informatie", 1)])


def _real_units():
    # Each unit is the heading plus the paragraphs around it — measured at ~749 chars.
    units = []
    for text, count in REAL:
        for i in range(count):
            units.append({"text": text, "context": "x" * (749 - len(text)), "doc": i})
    return units


def test_the_191_heading_case_does_not_fan_out():
    units = _real_units()
    assert len(units) == 191

    p = plan(units, key=lambda u: u["text"], render=lambda u: u["text"] + u["context"])

    assert p.n_units == 191
    assert p.n_distinct == 8
    assert not p.fans_out, (
        "the measured alternative spent 357,877 tokens across 5 agents to reach these same "
        "8 answers")
    assert len(p.shards[0]) == 8

    # The whole point: the eight representatives, context and all, are a rounding error next
    # to standing up even one agent -- and five agents were actually used.
    assert p.payload_tokens < 2_000               # 8 reps x ~749 chars = ~1,500 tokens
    assert p.payload_tokens < FIXED_COST_TOKENS // 20
    assert p.estimated_tokens * 4 < 357_877       # measured cost of the fan-out that happened


def test_even_without_dedupe_the_191_units_fit_one_agent():
    # Dedupe saved ~35,800 tokens here. NOT fanning out saved ~286,000. The gate has to hold
    # even when the units are all distinct, which is the case dedupe cannot help with.
    units = [{"text": f"heading {i}", "context": "x" * 740} for i in range(191)]
    p = plan(units, key=lambda u: u["text"], render=lambda u: u["text"] + u["context"])
    assert p.n_distinct == 191
    assert not p.fans_out
    assert p.payload_tokens < DEFAULT_BUDGET_TOKENS


def test_expand_returns_the_191_original_rows():
    units = _real_units()
    p = plan(units, key=lambda u: u["text"], render=lambda u: u["text"] + u["context"])
    labels = {k: "boilerplate" for k in p.groups}
    out = p.expand(labels)
    assert len(out) == 191 and all(v == "boilerplate" for v in out)
