"""The pure parts of the ledger's write path: how the world gets its identity.

No database. What is tested here is the digest, because the digest is the whole design:
a verdict is anchored to a facet's digest, so anything that changes how a digest is computed
silently un-anchors every verdict ever recorded.
"""
import json

import pytest

from scripts.claims import digest_of, probe_code, probe_graph


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
    # Without `dirty` in the payload, one digest would stand for HEAD plus every
    # uncommitted variant of it.
    clean = {"head": "047bd14", "branch": "main", "dirty": False, "dirty_files": 0}
    dirty = {"head": "047bd14", "branch": "main", "dirty": True, "dirty_files": 3}
    assert digest_of(clean) != digest_of(dirty)


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
    assert set(out) == {"head", "branch", "dirty", "dirty_files"}
    assert len(out["head"]) == 40 and isinstance(out["dirty"], bool)


@pytest.mark.parametrize("payload", [{}, {"a": None}, {"nested": {"b": [1, 2]}}])
def test_any_json_object_has_a_digest(payload):
    assert len(digest_of(payload)) == 64
