"""Decide how to run a judgement over many units — and usually, decide not to fan out.

    from tools.fanout import plan
    p = plan(units, key=lambda u: u["text"])
    print(p.why)
    for shard in p.shards:          # len(p.shards) == 1 most of the time
        ...

Why this exists, with the numbers that produced it
--------------------------------------------------
2026-11-12, judging 191 `tussenkop` headings. Five shard-judge agents were spawned, one per
`doc_type`. Measured, from their completion records:

    shard                   units   tokens      ms
    omgevingsvergunning         3   66,898  14,908
    evenementenvergunning       3   68,844  15,461
    exploitatievergunning      52   74,975  17,663
    andere vergunning          92   68,085  23,493
    verkeersbesluit            41   79,075  23,574
                                   -------
                                   357,877

Fit `tokens ~ a + b * units` over those five points: **R2 = 0.033**. An agent judging 3 units
cost 66,898 and one judging 92 cost 68,085. The cost of an agent is, in this range, entirely
**fixed** — about 71,600 tokens to stand one up, whatever you then ask it. The marginal cost of
a unit is lost in the noise.

Two separate wastes follow, and the smaller one is the obvious one:

    all 191 units inline, as payload        ~35,800 tokens
    the 8 distinct texts among them             ~30 tokens
    actually spent                          357,877 tokens

De-duplicating would have saved ~35,800. **Not fanning out would have saved ~286,000** — eight
times more. So this module's first question is "can one agent do it", and only its second is
"how much of this is repeated".

Fanning out buys wall-clock time, not tokens: those five ran in ~24 s against ~95 s serial.
That is 286,000 tokens for 71 seconds. It is worth it when a single agent genuinely cannot hold
the payload, and almost never otherwise.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

# An agent costs ~71,600 tokens to stand up before it reads a single unit (measured above).
FIXED_COST_TOKENS = 71_600

# What one agent can take as payload. A 200k window minus the fixed cost above, minus room for
# the agent's own reasoning and output. Deliberately conservative: the penalty for guessing low
# is one extra shard, and for guessing high is a truncated judgement nobody notices.
DEFAULT_BUDGET_TOKENS = 100_000

# Characters per token. Rough on purpose — the decision it feeds is 3x-scale, not 3%.
CHARS_PER_TOKEN = 4


def _digest(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


@dataclass
class Plan:
    units: Sequence[Any]
    key_of: Callable[[Any], Any]
    reps: list[Any]
    groups: "OrderedDict[str, list[int]]"
    payload_tokens: int
    shards: list[list[Any]]
    why: str
    budget_tokens: int = DEFAULT_BUDGET_TOKENS
    top: list[tuple[str, int]] = field(default_factory=list)

    @property
    def n_units(self) -> int:
        return len(self.units)

    @property
    def n_distinct(self) -> int:
        return len(self.groups)

    @property
    def fans_out(self) -> bool:
        return len(self.shards) > 1

    @property
    def estimated_tokens(self) -> int:
        return FIXED_COST_TOKENS * len(self.shards) + self.payload_tokens

    def expand(self, labels: dict[str, Any]) -> list[Any]:
        """Map a decision per DISTINCT key back onto every original unit, in input order.

        Raises on a key that was judged but never sent, or sent and never judged — a silent
        partial expansion would read as a complete result.
        """
        missing = [k for k in self.groups if k not in labels]
        if missing:
            raise KeyError(f"{len(missing)} distinct key(s) were sent but not judged: "
                           f"{missing[:3]}")
        extra = [k for k in labels if k not in self.groups]
        if extra:
            raise KeyError(f"{len(extra)} judged key(s) were never sent: {extra[:3]}")
        out: list[Any] = [None] * len(self.units)
        for k, idxs in self.groups.items():
            for i in idxs:
                out[i] = labels[k]
        return out

    def report(self) -> str:
        lines = [
            f"units            {self.n_units}",
            f"distinct         {self.n_distinct}"
            + (f"  ({self.n_units / self.n_distinct:.1f}x repeated)" if self.n_distinct else ""),
            f"payload          ~{self.payload_tokens:,} tokens (distinct only)",
            f"plan             {len(self.shards)} agent(s), "
            f"~{self.estimated_tokens:,} tokens estimated",
            f"why              {self.why}",
        ]
        if self.top:
            lines.append("most repeated:")
            lines += [f"   {c:>4}x  {k[:62]}" for k, c in self.top[:5]]
        return "\n".join(lines)


def dedupe(units: Sequence[Any], key: Callable[[Any], Any] | None = None):
    """Collapse units to one representative per distinct key, keeping input order."""
    key_of = key or (lambda u: u)
    groups: OrderedDict[str, list[int]] = OrderedDict()
    reps: list[Any] = []
    for i, u in enumerate(units):
        k = _digest(key_of(u))
        if k not in groups:
            groups[k] = []
            reps.append(u)
        groups[k].append(i)
    return reps, groups


def estimate_tokens(units: Iterable[Any], render: Callable[[Any], str] | None = None) -> int:
    r = render or (lambda u: u if isinstance(u, str) else repr(u))
    return sum(len(r(u)) for u in units) // CHARS_PER_TOKEN


def plan(units: Sequence[Any],
         key: Callable[[Any], Any] | None = None,
         render: Callable[[Any], str] | None = None,
         budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> Plan:
    """Work out how many agents this judgement needs. Usually one."""
    key_of = key or (lambda u: u)
    reps, groups = dedupe(units, key_of)
    payload = estimate_tokens(reps, render)

    if not units:
        shards, why = [], "nothing to judge"
    elif payload <= budget_tokens:
        shards = [list(reps)]
        saved = FIXED_COST_TOKENS * (max(1, round(payload / budget_tokens + 0.5)) - 1)
        why = (f"{len(reps)} distinct unit(s) are ~{payload:,} tokens, inside the "
               f"{budget_tokens:,}-token budget for one agent — fanning out would buy wall-clock "
               f"time at ~{FIXED_COST_TOKENS:,} tokens per extra agent and no accuracy")
        if saved:
            why += f" (~{saved:,} tokens)"
    else:
        n = -(-payload // budget_tokens)                      # ceil
        size = -(-len(reps) // n)
        shards = [list(reps[i:i + size]) for i in range(0, len(reps), size)]
        why = (f"{len(reps)} distinct unit(s) are ~{payload:,} tokens, over the "
               f"{budget_tokens:,}-token budget for one agent, so {len(shards)} shards of "
               f"<= {size}")

    counts = sorted(((groups[k], k) for k in groups), key=lambda t: -len(t[0]))
    top = []
    for idxs, k in counts[:8]:
        sample = key_of(units[idxs[0]])
        top.append((str(sample), len(idxs)))

    return Plan(units=units, key_of=key_of, reps=reps, groups=groups,
                payload_tokens=payload, shards=shards, why=why,
                budget_tokens=budget_tokens, top=top)
