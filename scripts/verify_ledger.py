"""Verify the ledger schema's load-bearing properties against a live database.

    .venv/bin/python -I scripts/verify_ledger.py

Assumes this repo's development Postgres container (`mapscale-pg`) is up; it shells out to
psql inside it, because psql is not installed on the host here.

Each check runs in its own transaction and is ROLLED BACK, so the live ledger -- which a
falsifier agent is writing to right now -- is never touched. Both streams are captured:
psql reports a constraint rejection on stderr, and reading only stdout made a correct
rejection look like a failed test earlier in this project.
"""
import subprocess
import sys

def psql(sql):
    p = subprocess.run(["docker", "exec", "-i", "mapscale-pg", "psql", "-U", "mapscale",
                        "-d", "mapscale", "-v", "ON_ERROR_STOP=0", "-At"],
                       input="BEGIN;\n" + sql + "\nROLLBACK;\n",
                       capture_output=True, text=True)
    return (p.stdout + p.stderr)

FIXTURE = """
INSERT INTO world_facet (name, probe) VALUES ('t_facet', 'test');
"""

MUST_REJECT = [
    ("status column is gone",
     "UPDATE claim SET status = 'survived' WHERE id = 1;", "status"),
    ("claim_stale(jsonb) is gone",
     "SELECT * FROM claim_stale('{}'::jsonb);", "claim_stale"),
    ("digest must look like a digest",
     FIXTURE + "INSERT INTO world_snapshot (facet,digest,payload) VALUES ('t_facet','xyz','{}');",
     "world_snapshot_digest_form"),
    ("payload must be an object",
     FIXTURE + "INSERT INTO world_snapshot (facet,digest,payload) VALUES ('t_facet','aaaaaaaaaaaa','[1]');",
     "world_snapshot_payload_is_object"),
    ("last_seen_at cannot precede first_seen_at",
     FIXTURE + "INSERT INTO world_snapshot (facet,digest,payload,first_seen_at,last_seen_at) "
     "VALUES ('t_facet','aaaaaaaaaaaa','{}', now(), now() - interval '1 day');",
     "world_snapshot_seen_order"),
    ("a facet must be registered",
     "INSERT INTO world_snapshot (facet,digest,payload) VALUES ('weather','aaaaaaaaaaaa','{}');",
     "world_snapshot_facet_fkey"),
    ("a topic must be registered",
     "INSERT INTO claim (topic,text,made_by) VALUES ('Search','x','t');",
     "claim_topic_fkey"),
    ("a verdict cannot anchor to a snapshot that is not there",
     "INSERT INTO verdict_world (verdict_id,snapshot_id) VALUES (9, 999999);",
     "verdict_world_snapshot_id_fkey"),
]

MUST_HOLD = [
    ("re-probing an unchanged facet does not create a row",
     FIXTURE + """
     INSERT INTO world_snapshot (facet,digest,payload) VALUES ('t_facet','aaaaaaaaaaaa','{}');
     INSERT INTO world_snapshot (facet,digest,payload) VALUES ('t_facet','aaaaaaaaaaaa','{}')
       ON CONFLICT (facet,digest) DO UPDATE SET last_seen_at = now() + interval '1 s';
     SELECT 'rows=' || count(*) || ' advanced=' || (max(last_seen_at) > max(first_seen_at))
     FROM world_snapshot WHERE facet='t_facet';""",
     "rows=1 advanced=t"),

    ("a world that goes A -> B -> A has A as current, not B",
     FIXTURE + """
     INSERT INTO world_snapshot (facet,digest,payload,first_seen_at,last_seen_at)
       VALUES ('t_facet','aaaaaaaaaaaa','{}', now()-interval '3 h', now()-interval '3 h'),
              ('t_facet','bbbbbbbbbbbb','{}', now()-interval '2 h', now()-interval '2 h');
     INSERT INTO world_snapshot (facet,digest,payload) VALUES ('t_facet','aaaaaaaaaaaa','{}')
       ON CONFLICT (facet,digest) DO UPDATE SET last_seen_at = now();
     SELECT 'current=' || digest FROM world_current WHERE facet='t_facet';""",
     "current=aaaaaaaaaaaa"),

    ("verdict_stale() fires when the anchored facet has moved",
     FIXTURE + """
     INSERT INTO world_snapshot (facet,digest,payload,first_seen_at,last_seen_at)
       VALUES ('t_facet','aaaaaaaaaaaa','{}', now()-interval '2 h', now()-interval '2 h'),
              ('t_facet','bbbbbbbbbbbb','{}', now(), now());
     INSERT INTO verdict_world (verdict_id, snapshot_id)
       SELECT 9, id FROM world_snapshot WHERE facet='t_facet' AND digest='aaaaaaaaaaaa';
     SELECT 'stale=' || count(*) || ' facet=' || coalesce(max(facet),'-')
     FROM verdict_stale() WHERE facet='t_facet';""",
     "stale=1 facet=t_facet"),

    ("and does NOT fire while it is unchanged",
     FIXTURE + """
     INSERT INTO world_snapshot (facet,digest,payload) VALUES ('t_facet','aaaaaaaaaaaa','{}');
     INSERT INTO verdict_world (verdict_id, snapshot_id)
       SELECT 9, id FROM world_snapshot WHERE facet='t_facet';
     SELECT 'stale=' || count(*) FROM verdict_stale() WHERE facet='t_facet';""",
     "stale=0"),

    # The first version of this case asserted only "0 anchors after the delete" -- and
    # verdict_world was empty to begin with, so it passed while testing nothing. It now
    # creates the anchor and asserts the before AND after in one string.
    ("deleting a verdict takes its anchors with it",
     FIXTURE + """
     INSERT INTO world_snapshot (facet,digest,payload) VALUES ('t_facet','aaaaaaaaaaaa','{}');
     INSERT INTO verdict_world (verdict_id, snapshot_id)
       SELECT 9, id FROM world_snapshot WHERE facet='t_facet';
     SELECT 'before=' || count(*) FROM verdict_world WHERE verdict_id = 9;
     DELETE FROM verdict WHERE id = 9;
     SELECT 'after=' || count(*) FROM verdict_world WHERE verdict_id = 9;""",
     "before=1"),

    ("status is derived, and the two contradictory rows are gone",
     "SELECT id || '=' || status FROM claim_current WHERE id IN (1,2,3) ORDER BY id;",
     "1=destroyed"),
]

bad = 0
print("=== must be rejected ===")
for name, sql, needle in MUST_REJECT:
    out = psql(sql)
    ok = needle in out
    bad += not ok
    first = next((ln for ln in out.splitlines() if "ERROR" in ln or needle in ln),
                 out.strip()[:80])
    print(f"  {'ok  ' if ok else 'FAIL'} {name:52} {first[:86]}")

print("\n=== must hold ===")
for name, sql, needle in MUST_HOLD:
    out = psql(sql)
    ok = needle in out and "ERROR" not in out
    bad += not ok
    got = " / ".join(ln for ln in out.splitlines() if ln.strip())[:86]
    print(f"  {'ok  ' if ok else 'FAIL'} {name:52} {got}")

print(f"\n{'ALL PASS' if bad == 0 else str(bad) + ' FAILURES'}")
sys.exit(1 if bad else 0)
