# AI dev framework

How this repo constrains an AI agent working in it, why each piece exists, and what is still
missing. Written so that someone — or some session — can pick it up without having been
present when it was built.

Status as of 2026-11-13, end of day: three mechanisms exist and work, and the ledger now has a
write path that has been walked end to end once — on the first walk it destroyed a number that
was already shipped in two notes and a code comment. What is still missing is *enforcement* of
that path: nothing makes you use it. See **Gaps** before building on any of it.

---

## 1. The problem this addresses

This project's expensive failures have never been missing features. They are **conclusions that
were wrong while looking right**, and the record of them is long enough to be a pattern rather
than bad luck:

| the conclusion | what was actually true | how long it stood |
|---|---|---|
| all four `cycling-*` graphs are broken | one bad snap point, seen four times | 10 days |
| a publication carries 2.5 geometries | 1.72 — measured at 10,342 rows, corpus grew to 94,183 | 12 days |
| the load test sustains 2,736 rps | 82,110 consecutive 404s counted as success | one session |
| `tussenkop` is 6.3% of body text, all boilerplate | 0.6%, and Amsterdam-only | 2 days |
| plausibility check should key on `reachfactor > 0.15` | that gate degrades every driving isochrone | caught before shipping |

Every one was measured. Every one was wrong in a way that a careful reader of the conclusion
could not detect, because the error was in **what the measurement was taken against**, not in
the arithmetic.

An AI agent makes this worse in one specific way: it is fluent. A wrong conclusion arrives with
a plausible mechanism, a table, and a confident scope line. The usual defence — "that doesn't
sound right" — fires less often.

So the framework's goal is not to make the agent smarter. It is to make **a wrong conclusion
expensive to record and easy to find later**.

---

## 2. Architecture: three layers, and they are not equally binding

This is the part most worth internalising. The layers differ in **enforcement**, and putting a
rule in the wrong layer is the most common way this kind of system quietly stops working.

| layer | lives in | binding? |
|---|---|---|
| knowledge | `CLAUDE.md`, `docs/pg-notes/`, agent definitions | the model reads it and mostly complies |
| judgement | `.claude/agents/*.md` (falsifier, shard-judge) | invoked when the model decides to |
| enforcement | `.claude/hooks/guard.py` + `.claude/settings.json` | **the harness runs it; the model cannot skip it** |

Only the third is deterministic. A rule that must hold belongs there. A rule that merely
*should* hold is fine in the first, as long as nobody believes it is the third.

Crossing the layers is where the value is: the guard hook enforces things CLAUDE.md documents,
and the `shard-judge` gate is a hook rule backing a number that `scripts/fanout.py` computes.

---

## 3. What exists

### 3.1 The guard — `.claude/hooks/guard.py`

A `PreToolUse` hook on `Bash|Write|Edit|Agent`. 186 lines, 42 tests in `test_guard.py`.

Seven rules, each from a documented "do not" or from something that happened: remote branch
deletion, history rewriting, `compose down -v`, hard reset/clean, editing an applied migration,
constructing an `httpx.AsyncClient` under `api/main/`, dropping `plan_cache_mode`, and a
`shard-judge` spawned with no fan-out plan.

Four properties, each one forced by a failure rather than designed in:

- **Silence means "no rule matched".** It never emits `permissionDecision: "allow"`, which
  would bypass the normal permission flow and make the guard a back door.
- **Mentioning a command is not running it.** v1 blocked `grep -rn '<a blocked command>' docs/`.
  Patterns now require command position.
- **A heredoc body is data.** The guard blocked its own commit — the message quoted blocked
  commands as examples, and a heredoc body is part of the Bash tool's `command` string.
- **It fails open but never silently.** A bug inside it prints a `systemMessage` saying the
  checks were skipped.

The last one is the load-bearing property. While rewriting path matching, an undefined name
made **every path rule silently pass** while the tests printed `silent` — which is exactly what
a correct pass looks like. A cwd-independence check caught it. A guard that quietly stops
enforcing is worse than no guard, because the rules still look live.

**The must-NOT-fire tests are the important half.** A guard with false positives gets switched
off, and then it protects nothing.

### 3.2 The falsifier — `.claude/agents/falsifier.md`

An agent whose only job is to destroy a measured conclusion before it becomes durable. 176
lines, and it carries its own catalogue of mistake families inline so that a cold-started
subagent has them.

Contract — it refuses rather than guesses if any of these is missing:

1. the conclusion in one sentence
2. the exact commands that produced it
3. paths to the raw output
4. **the axis you believe you varied**

Returns `DESTROYED | SURVIVED | REFUSED`, objections each carrying a command it ran and the
bytes it got back, an `UNTESTED` section, and a `SCOPE` line — the narrowest form of the claim
its evidence does not break. **That line is the deliverable**; it is what gets pasted into the
notes instead of the original wording.

Two design choices worth keeping:

- **Organised by mistake family, not as a checklist.** The project's own record shows the same
  error arriving in new disguises — the loadtest note says "this is the third time 2xx has been
  taken as evidence of correct work". A checklist matches bugs you have seen; families match
  the next disguise.
- **Rule zero applies to the falsifier itself.** Every objection must carry a command it
  actually ran. An objection reasoned to but not executed is dropped. Without this it produces
  "you may want to double-check this", which is worse than nothing.

Measured behaviour: two runs in one session, both `DESTROYED`. The second one did not just
correct numbers — it **inverted the character of the finding** (from "returns an empty result"
to "returns a plausible wrong answer, sometimes larger than the correct one"), by closing an
axis the caller had explicitly flagged as untested.

### 3.3 The fan-out planner — `scripts/fanout.py`

Decides how many agents a judgement needs. The answer is almost always one, and the reason is
counter-intuitive enough that it has to be stated with the numbers:

```
units   tokens          tokens ~ 70,609 + 25.3 * units,  R2 = 0.033
    3   66,898
    3   68,844          An agent judging 3 units cost 66,898.
   52   74,975          One judging 92 cost 68,085.
   92   68,085          The cost of an agent is FIXED: ~71,600 to stand one up.
   41   79,075
       -------
       357,877          to reach 8 distinct answers
```

So the waste has two parts and the obvious one is the small one:

```
all 191 units inline, as payload      ~35,800 tokens
the 8 distinct texts among them           ~30 tokens
actually spent                        357,877 tokens
```

De-duplicating saves ~35,800. **Not fanning out saves ~286,000.** Fan-out buys wall-clock time
and nothing else (~24 s against ~95 s serial). Hence `plan()` asks "can one agent hold this"
first, "how much is repeated" second, and its budget is a **context** budget, not a unit count.

The gate is in two places so it is not another thing to remember: `shard-judge`'s description
requires `plan.report()` as a fifth input, and the guard **asks** when one is spawned without
it.

### 3.4 The ledger — `migrations/0021`–`0024`, `scripts/claims.py`

`claim` / `evidence` / `verdict`, plus `world_facet` / `world_snapshot` / `verdict_world` /
`topic`. **`scripts/claims.py` is the only way in**, and the falsifier writes its own verdict
through it so that the `SCOPE` line is not paraphrased on the way to the record.

**The world is a content-addressed snapshot, not a set of keys.** A facet is probed, the whole
probe output is hashed, and a verdict is anchored to the digests it was reached under. Different
digest ⇒ untested, and nothing has to know why. This replaced `claim_stale(jsonb)` — dropped in
0024 — for three reasons, each of them a measured failure of the key-comparison design:

- It needed its caller to assemble the current world. Nothing did: **0 callers in 12 days**, and
  the one stale number it existed to catch was found by hand anyway.
- A key can be under-specified and still look fine. A claim recorded
  `graph_date: 2026-10-28T16:45:20Z`, which is **cycling-regular's** build date; the three
  profiles carry three, so rebuilding only `driving-car` left that key matching.
- "Could not be read" had to be told apart from "has not changed" — the whole of 0022. An
  unreachable ORS made every graph claim read as current. Unreachability is now a payload, so it
  hashes *differently* from an answer: incompleteness is a visible difference, not a silent pass.

The cost is real: a digest is all-or-nothing where a key comparison was precise. Hence facets,
and `--facets` to narrow a verdict to the ones its evidence actually depended on.

**`claim.status` is not a column** (0024). It was one, and nothing ever wrote it: all three
hand-inserted rows said `survived` while two had a latest verdict of `DESTROYED`, and
`claim_stale` filtered on `status IN ('open','survived')`, so both refuted claims were still
being checked as live. It is derived in `claim_current` now. Dropping the column beats a
trigger, which can be disabled while the column stays readable.

`claim.context` keeps its gin index but holds the **experiment's** parameters only; the world's
dimensions moved to facets. The old rows mixed the two, which is why `claim_stale` had to guess.

Constraints exist where the alternative lies: `evidence_path_needs_hash` (a row naming a file
with no digest outlives the bytes — and `claims.py` hashes the file rather than taking your word
for the digest), `verdict_scope_unless_refused`, and `topic`/`world_facet` as closed sets with
FKs, because free text lets `search` and `Search` diverge and a per-topic staleness check then
returns zero rows, which reads exactly like "nothing is stale".

Verify the schema against a live database with `scripts/verify_ledger.py` — 14 checks, each in a
rolled-back transaction, reading **both** streams because psql reports a constraint rejection on
stderr and reading only stdout once made a correct rejection look like a failed test.

---

## 4. Gaps — read this before building on the above

### 4.1 Closed — the ledger has a write path, and walking it destroyed a shipped number

What this section used to say: nothing wrote to these tables; the three rows were inserted by
hand; `claim.status` said `survived` for all three while `verdict` said two were `DESTROYED`;
`claim_stale()` filtered on status, so two refuted claims were still being checked as live.

Closed by `scripts/claims.py` (the single entry point) and 0024 (status derived, not stored).
The two contradictory rows cannot recur, because there is no column to write wrongly:
`UPDATE claim SET status = …` now fails with *column "status" does not exist*.

**What the first real walk produced**, because it is the only evidence that any of this works:
claim 4, `the vector arm beats FTS-over-body by +0.194 nDCG@10 … 95% CI [+0.024, +0.361]`, a
number that was **already shipped** in `semsearch-judgeset.md`, `semantic-search.md` and a
comment in `api/main/domains/search/service.py`. The falsifier destroyed it and wrote verdict 12
itself. Three independent checks: Holm across the five comparisons the scorer itself prints
(p=0.0295 vs threshold 0.0167), counting byte-identical stored body texts once (+0.108, CI
covering zero), and resolving each arm's exact-score ties in its own favour (+0.137, CI covering
zero). Mechanism: `ts_rank_cd` emits a median of **2 distinct scores across `fts_body`'s whole
top 10**, so nDCG@10 was largely scoring an arbitrary SQL tie order. Surviving form: direction
holds, magnitude unresolved. All three places were corrected to the `SCOPE` line.

One sub-gap from that section is still open: `claim.scope` holds the pre-refutation wording
while the current form sits in `verdict.scope`, and which is authoritative is still undefined.
In practice `verdict.scope` is the one being read.

### 4.2 Closed differently from how this document designed it

This section proposed a `world_state` table, "one row per dimension with a `measured_at`". That
is not what got built, and the reason is worth keeping: a per-dimension table has the same three
faults as `claim_stale(jsonb)` — somebody has to know which dimensions matter, a dimension can
be under-specified and still compare equal, and "could not be read" has to be distinguished from
"unchanged" by a special case. Content-addressed facets have none of those; see §3.4.

**A new gap the walk opened, measured.** `probe_code` hashes `dirty_files`, so the `code` digest
moved **three times in one session** (5 → 7 → 9 dirty files) while the measurement's frozen input
stayed byte-identical. Then, after the notes were corrected per verdict 12's own `SCOPE`, that
very verdict went stale:

```
verdict 12  claim 4  [search]  DESTROYED
  code: cb457d4dbfe1 -> 2b145620ca71          # judgeset-scores.json sha256 unchanged
```

Writing the correction the verdict asked for is what expired the verdict. `code` as currently
probed is a false-staleness generator: it is issuing an identity to an uncommitted working tree.
The fix is already half-present — for a measurement over frozen inputs the right anchor is the
digest of those inputs, which `evidence.sha256` carries. **Owner: unassigned.** Until then,
anchor exploratory work with `--facets none` and accept that it never expires, or commit first.

### 4.3 Nothing surfaces stale claims at the moment they matter

`claims.py stale` has to be run by someone who thinks to run it, and nothing forces anyone
through `claims.py` at all. Two gates were designed for this and deliberately **not** built:
`INSERT INTO claim|verdict` outside the entry point → deny, and a `Stop` gate refusing to let a
session end with an open claim. The second was dropped on inspection — `Stop` fires at the end of
**every turn**, not of the session, so it would hold every later turn hostage, and a block an
agent cannot satisfy (Postgres down) is a hang. The first is worth building, with one measured
caveat: the analogous migration rule covers only the `Write`/`Edit` tools, and three Bash routes
to the same file (`cat >`, `sed -i`, `tee`) pass it silently — verified 2026-11-13, and both
migrations in this very change were created through one of them. A regex over Bash is a reminder,
not a boundary; the boundary is `REVOKE INSERT`.

The intervention with the best ratio remains a **`SessionStart` hook** that runs it and injects
the result through
`hookSpecificOutput.additionalContext`: an agent then starts work already knowing which of the
repo's recorded numbers no longer hold.

A Grafana panel over the same data is cheap (the container exists; it needs one datasource yml)
but solves a different problem — it shows a human a trend, where the hook stops an agent from
quoting a dead number. If only one gets built, build the hook.

### 4.4 Unverified and partly-verified things

- The `Agent` matcher rule in the guard is unit-tested but has never fired on a real
  `shard-judge` spawn.
- `scripts/fanout.py` has never been used to plan a real fan-out. Its model comes from one
  five-agent run; `FIXED_COST_TOKENS` should be re-derived once there is a second data point,
  and it is almost certainly model- and prompt-dependent.
- The falsifier has been run four times. "It does not produce filler" is a claim with n=4 — the
  fourth run is the strongest evidence so far, since it destroyed a shipped number and the
  objections reproduce bit-identically from its own committed script, but n=4 is n=4.
- The ledger's write path has been walked **once**. Every number in §4.1 comes from that one
  walk.

---

## 5. Principles, with the evidence that produced them

These transfer to other projects. The specifics above mostly do not.

1. **Put a rule in the layer whose enforcement matches how much you need it.** A "must" in a
   markdown file is a "usually".
2. **A mechanism that fails open must fail loudly.** The silent-pass bug in the guard is the
   canonical case: every rule stopped working and the tests still printed `silent`. This is the
   same shape as the project's older lesson that an empty 200 is more dangerous than a 504.
3. **False positives are what kill a check**, not false negatives. A guard that blocks
   legitimate work gets switched off; a staleness report with one noisy row in three gets
   skimmed, and the one real entry is skimmed with it. Every rule needs a must-NOT-fire test.
4. **Name the axis you did not vary.** Three of the five failures in §1 are this. The falsifier
   demands it as an input for exactly that reason.
5. **A measured result has a scale of validity.** Record the scale with the number, or the
   number will outlive it by twelve days.
6. **Couple the storage, generalise the contract.** The ledger's value is that it holds
   domain dimensions a generic schema would drop. The falsifier's contract — four inputs, a
   fixed verdict shape — is generic and already portable. Do not swap those round.
7. **Verify the instrument before the subject.** In one session: a load generator that was
   itself the bottleneck, a test harness that discarded stderr so a correct rejection read as a
   failure, another that built 40 identical strings for a test about 40 distinct ones, and a
   shell `echo` that mangled three payloads. The instrument is wrong more often than feels
   plausible.
8. **Measure before optimising a cost you assume.** Everyone's instinct about the fan-out was
   de-duplication. The measurement said the de-duplication was worth an eighth of just not
   fanning out.

---

## 6. Anti-patterns

- **A pyramid of role-playing agents** (PM → architect → backend → QA). It simulates a
  communication structure this project does not have, and does not address the actual failure
  mode, which is that a conclusion is wrong, not that too little code gets written. Five
  role-playing agents will believe the same HTTP 200.
- **Parallel agents writing the same files.** A typical change here touches a router, a domain
  service, an upstream module, a test and a note — five coupled files. Coordination costs more
  than it saves.
- **A dashboard nobody opens.** Worse than no dashboard: it creates the belief that something
  is being watched.
- **Fan-out for work a script does.** Fetching, counting, sorting, de-duplicating. The measured
  cost of an agent is ~71,600 tokens before it does anything.
- **Generalising the ledger out of the project.** The coupling is the value; see principle 6.

---

## 7. File index

| path | what |
|---|---|
| `.claude/settings.json` | hook wiring — `PreToolUse` on `Bash\|Write\|Edit\|Agent` |
| `.claude/hooks/guard.py` | the rules, deterministic |
| `.claude/hooks/test_guard.py` | 42 cases; the must-NOT-fire half matters most |
| `.claude/agents/falsifier.md` | the destroyer, with its own mistake catalogue inline |
| `.claude/agents/shard-judge.md` | one shard of a fan-out, judgement only |
| `scripts/fanout.py` | how many agents this needs (usually one) |
| `tests/unit/test_fanout.py` | 14 cases incl. the real 191-unit distribution |
| `migrations/0021_claim_evidence.sql` | the ledger |
| `migrations/0022_claim_stale_ignores_nulls.sql` | superseded by 0023; kept, forward-only |
| `migrations/0023_world_snapshot.sql` | the world as content-addressed facets |
| `migrations/0024_claim_status_derived.sql` | `status` stops being a column |
| `scripts/claims.py` | the only way into the ledger |
| `scripts/verify_ledger.py` | 14 schema checks, all rolled back |
| `tests/unit/test_claims.py` | 11 cases; the digest is the contract |
| `CLAUDE.md` § Measurement discipline | when to run the falsifier, what to record |
| `CLAUDE.md` § Enforced rules | what the guard decides and why |
| `CLAUDE.md` § Fanning out | the cost model |
| `docs/pg-notes/` | the raw output behind every number quoted here |
