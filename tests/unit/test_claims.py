"""The pure parts of the ledger's write path: how the world gets its identity.

No database. What is tested here is the digest, because the digest is the whole design:
a verdict is anchored to a facet's digest, so anything that changes how a digest is computed
silently un-anchors every verdict ever recorded.
"""
import json

import pytest

from etl.ledger.claims import digest_of, probe_code, probe_graph, probe_paths


# ── the digest is the contract ───────────────────────────────────────────────

def test_key_order_cannot_change_the_identity():
    # Two probes of an unchanged world must not look like a change just because a dict
    # happened to iterate differently.
    a = {"bekendmaking_rows": 94183, "bekendmaking_locatie_rows": 160477}
    b = {"bekendmaking_locatie_rows": 160477, "bekendmaking_rows": 94183}
    assert digest_of(a) == digest_of(b)


def test_the_canonical_form_is_pinned():
    # A GOLDEN digest, on purpose. If the canonicalisation ever changes -- separators,
    # sort order, default= -- every verdict in the ledger reads as stale at once, which
    # looks exactly like "the whole world moved" and is unrecoverable after the fact.
    # This test is what makes that change loud instead of silent.
    assert digest_of({"a": 1, "b": "x"}) == digest_of({"b": "x", "a": 1})
    assert json.dumps({"b": "x", "a": 1}, sort_keys=True, separators=(",", ":")) == '{"a":1,"b":"x"}'
    assert digest_of({"a": 1, "b": "x"}).startswith("ecf9e98e")


def test_unreachable_hashes_differently_from_an_answer():
    # The reason this design replaced claim_stale(jsonb). 0022 had to special-case "could
    # not be read" so it would not count as a change; here it IS a different world, because
    # a verdict reached while ORS was down was reached under different conditions.
    down = {"url": "u", "reachable": False, "error": "URLError: refused"}
    up = {"url": "u", "reachable": True, "engine": "10.1.0", "profiles": {}}
    assert digest_of(down) != digest_of(up)


def test_one_profile_moving_is_a_different_world():
    # The under-specification that a single `graph_date` key could not express: three
    # profiles carry three build dates, so rebuilding only driving-car has to register.
    base = {"profiles": {
        "foot-hiking":     {"graph_build_date": "2026-10-28T16:43:51Z"},
        "driving-car":     {"graph_build_date": "2026-10-28T16:44:44Z"},
        "cycling-regular": {"graph_build_date": "2026-10-28T16:45:20Z"}}}
    moved = json.loads(json.dumps(base))
    moved["profiles"]["driving-car"]["graph_build_date"] = "2026-11-13T09:00:00Z"
    assert digest_of(base) != digest_of(moved)

    # ... and the key that COULD NOT see it, for contrast: keying on cycling-regular alone
    # compares equal across that rebuild. This is the bug, written as a test.
    assert (base["profiles"]["cycling-regular"]["graph_build_date"]
            == moved["profiles"]["cycling-regular"]["graph_build_date"])


def test_a_dirty_tree_is_not_the_same_world_as_its_HEAD():
    # Without the worktree term, one digest would stand for HEAD plus every uncommitted
    # variant of it.
    clean = {"head": "047bd14", "tree": "t", "dirty": False, "worktree_sha256": None}
    dirty = {"head": "047bd14", "tree": "t", "dirty": True, "worktree_sha256": "a" * 64}
    assert digest_of(clean) != digest_of(dirty)


def test_a_count_of_dirty_files_cannot_be_an_identity():
    # 0023 hashed `dirty_files`. Two completely different working trees with the same number
    # of changed files collided; this is the regression that cost the project a facet. The
    # content digest tells them apart.
    a = {"head": "h", "tree": "t", "dirty": True, "dirty_files": 3}
    b = {"head": "h", "tree": "t", "dirty": True, "dirty_files": 3}
    assert digest_of(a) == digest_of(b)          # the bug, as a test
    a2 = {"head": "h", "tree": "t", "dirty": True, "worktree_sha256": "a" * 64}
    b2 = {"head": "h", "tree": "t", "dirty": True, "worktree_sha256": "b" * 64}
    assert digest_of(a2) != digest_of(b2)        # the fix


def test_branch_is_not_part_of_the_code_identity():
    # 0023 put `branch` in the payload, so `git checkout -b` expired every verdict anchored
    # to `code` while the code was byte-identical.
    assert "branch" not in probe_code()


# ── path facets: a facet defined by the bytes a measurement reads ────────────

def test_a_path_facet_digests_the_files_it_declares():
    out = probe_paths(["CLAUDE.md", "etl/ledger/claims.py"])
    assert set(out["files"]) == {"CLAUDE.md", "etl/ledger/claims.py"}
    assert out["absent"] == []
    assert all(len(v) == 64 for v in out["files"].values())


def test_a_missing_declared_path_is_recorded_not_skipped():
    # Dropping an absent file would make a facet whose inputs were DELETED compare equal to
    # one where they are intact -- the same shape as an unreachable ORS comparing equal to a
    # reachable one.
    out = probe_paths(["CLAUDE.md", "docs/pg-notes/does-not-exist.json"])
    assert out["files"]["docs/pg-notes/does-not-exist.json"] == "ABSENT"
    assert out["absent"] == ["docs/pg-notes/does-not-exist.json"]
    assert digest_of(out) != digest_of(probe_paths(["CLAUDE.md"]))


def test_a_path_facet_ignores_files_it_did_not_declare():
    # The whole point: this is why claim 4 should anchor to `judgeset` and not to `code`.
    # Editing a note must not expire a measurement that never read it.
    before = digest_of(probe_paths(["etl/ledger/claims.py"]))
    assert before == digest_of(probe_paths(["etl/ledger/claims.py"]))
    assert before != digest_of(probe_paths(["etl/ledger/claims.py", "CLAUDE.md"]))


def test_digest_satisfies_the_column_constraint():
    # world_snapshot_digest_form CHECK (digest ~ '^[0-9a-f]{12,64}$')
    d = digest_of({"x": 1})
    assert len(d) == 64 and all(c in "0123456789abcdef" for c in d)


# ── probes degrade, they do not raise ───────────────────────────────────────

def test_an_unreachable_ors_is_a_payload_not_an_exception(monkeypatch):
    # A probe that raises would abort the snapshot, and then nothing gets recorded at all
    # -- the measurement would proceed with no anchor rather than with an honest one.
    monkeypatch.setenv("ORS_BASE_URL", "http://127.0.0.1:1/ors/v2")
    out = probe_graph()
    assert out["reachable"] is False
    assert "error" in out and out["url"].endswith("/status")


def test_probe_code_reports_the_fields_the_digest_depends_on():
    out = probe_code()
    assert set(out) == {"head", "tree", "dirty", "worktree_sha256"}
    assert len(out["head"]) == 40 and len(out["tree"]) == 40
    assert isinstance(out["dirty"], bool)
    # The worktree term is present exactly when the tree is dirty, so a clean tree's identity
    # is just its commit and cannot drift.
    assert (out["worktree_sha256"] is not None) == out["dirty"]


@pytest.mark.parametrize("payload", [{}, {"a": None}, {"nested": {"b": [1, 2]}}])
def test_any_json_object_has_a_digest(payload):
    assert len(digest_of(payload)) == 64
