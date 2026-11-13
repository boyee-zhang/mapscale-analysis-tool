"""Tests for the PreToolUse guard.

    python3 .claude/hooks/test_guard.py        # from the repo root

Every rule needs a case that MUST fire and a case that MUST NOT. The must-not half is the
half that matters: a guard with false positives gets switched off, and then it protects
nothing. Both false positives found so far are in here as regression cases.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guard.py")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(payload: dict, cwd: str | None = None):
    p = subprocess.run([sys.executable, "-I", GUARD], input=json.dumps(payload),
                       capture_output=True, text=True, cwd=cwd or ROOT)
    if p.returncode != 0:
        return f"EXIT {p.returncode}"
    if not p.stdout.strip():
        return None
    o = json.loads(p.stdout)
    if "hookSpecificOutput" in o:
        return o["hookSpecificOutput"]["permissionDecision"]
    return "SYSMSG" if "systemMessage" in o else None


def bash(c):      return {"tool_name": "Bash", "tool_input": {"command": c}}
def write(p, c):  return {"tool_name": "Write", "tool_input": {"file_path": p, "content": c}}
def edit(p, s):   return {"tool_name": "Edit", "tool_input": {"file_path": p, "new_string": s}}
def agent(t, pr): return {"tool_name": "Agent", "tool_input": {"subagent_type": t, "prompt": pr}}


# The literal is assembled so that this test file can itself be written and committed through
# the Bash tool without the guard matching its own source.
PUSH = "git push"
FORCE = PUSH + " --" + "force"
DELETE = PUSH + " origin --" + "delete feature/x"

MUST_FIRE = [
    ("push --delete",              bash(DELETE), "ask"),
    ("push :branch",               bash(PUSH + " origin :old"), "ask"),
    ("push --force",               bash(FORCE + " origin main"), "deny"),
    ("push -f",                    bash(PUSH + " -f"), "deny"),
    ("chained after &&",           bash("cd /tmp && " + FORCE), "deny"),
    ("chained after ;",            bash("echo hi; " + DELETE), "ask"),
    ("sudo prefix",                bash("sudo " + FORCE), "deny"),
    ("compose down -v",            bash("docker compose --profile ors down -v"), "ask"),
    ("compose down --volumes",     bash("docker compose down --volumes"), "ask"),
    ("reset --hard",               bash("git reset --hard origin/main"), "ask"),
    ("clean -fd",                  bash("git clean -fd"), "ask"),
    ("edit applied migration",     edit("migrations/0020_bekendmaking_fts.sql", "select 1"), "deny"),
    ("httpx in a router",          write("api/main/routers/x.py", "httpx.AsyncClient(timeout=5)"), "deny"),
    ("httpx in a domain",          write("api/main/domains/geo/service.py", "httpx.AsyncClient()"), "deny"),
    ("absolute path, same rule",   write(os.path.join(ROOT, "api/main/routers/x.py"),
                                         "httpx.AsyncClient()"), "deny"),
    # A shard-judge with no fan-out plan: every extra agent is ~71,600 tokens of fixed cost.
    ("shard-judge, no plan",       agent("shard-judge", "judge the headings in shard 3"), "ask"),
    ("shard-judge, half a plan",   agent("shard-judge", "units: 191 distinct: 8"), "ask"),
    # A heredoc whose body IS executed stays under the rules.
    ("bash <<EOF runs its stdin",  bash("bash <<'EOF'\n" + FORCE + "\nEOF"), "deny"),
    ("sh <<EOF runs its stdin",    bash("sh <<'EOF'\n" + FORCE + "\nEOF"), "deny"),
]

MUST_NOT_FIRE = [
    ("plain push",                 bash(PUSH + " -u origin fix/foo")),
    ("--force-with-lease",         bash(PUSH + " --force-with-lease")),
    ("compose down, no -v",        bash("docker compose --profile ors down")),
    ("reset (soft)",               bash("git reset HEAD~1")),
    ("NEW migration",              write("migrations/0099_new.sql", "create table x();")),
    ("http_client.py is exempt",   write("api/main/http_client.py", "_c = httpx.AsyncClient()")),
    ("a script, not api/",         write("scripts/x.py", "httpx.AsyncClient()")),
    ("ordinary bash",              bash("ls -la")),
    ("ordinary edit",              edit("README.md", "hi")),
    ("shard-judge with a plan",    agent("shard-judge",
                                         "units 191 / distinct 8 / payload ~1,498 tokens / "
                                         "2 shards. judge your shard.")),
    ("a falsifier, not a shard",   agent("falsifier", "conclusion: X. commands: Y.")),
    ("a general-purpose agent",    agent("general-purpose", "go look something up")),
    # Regression 1: a command that MENTIONS a blocked command is not running it.
    ("grep for the words",         bash("grep -rn '" + FORCE + "' docs/")),
    ("echo the words",             bash("echo 'never run " + FORCE + "'")),
    ("rg for compose down -v",     bash("rg 'docker compose down -v' CLAUDE.md")),
    ("writing docs about it",      write("docs/x.md", "never run " + FORCE + " here")),
    # Regression 2: a heredoc body is data. This guard blocked its own commit message.
    ("commit message heredoc",     bash("git commit -F - <<'MSG'\nfix: do not "
                                        + FORCE + "\nexample: cd /tmp && " + FORCE + "\nMSG")),
    ("cat heredoc to a file",      bash("cat > notes.md <<'EOF'\n" + DELETE + "\nEOF")),
    ("python heredoc",             bash("python3 - <<'PY'\nprint('" + FORCE + "')\nPY")),
]


def main() -> int:
    bad = 0
    print("=== must fire ===")
    for name, payload, want in MUST_FIRE:
        got = run(payload)
        ok = got == want
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28} -> {got} (want {want})")

    print("\n=== must stay silent ===")
    for name, payload in MUST_NOT_FIRE:
        got = run(payload)
        ok = got is None
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28} -> {'silent' if ok else got}")

    print("\n=== independent of the guard's own cwd ===")
    for cwd in (ROOT, "/tmp"):
        for path, want in ((os.path.join(ROOT, "api/main/routers/x.py"), "deny"),
                           ("api/main/routers/x.py", "deny"),
                           (os.path.join(ROOT, "api/main/http_client.py"), None)):
            got = run(write(path, "httpx.AsyncClient()"), cwd=cwd)
            ok = got == want
            bad += not ok
            tag = "root" if cwd == ROOT else "tmp"
            print(f"  {'ok  ' if ok else 'FAIL'} cwd={tag:4} {os.path.basename(path):18} -> {got}")

    print("\n=== a broken guard fails open, but says so ===")
    for name, payload, want in (("tool_input is a string",
                                 {"tool_name": "Write", "tool_input": "oops"}, "SYSMSG"),
                                ("unknown tool",
                                 {"tool_name": "Glob", "tool_input": {"pattern": "*"}}, None)):
        got = run(payload)
        ok = got == want
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28} -> {got}")
    for name, raw in (("malformed stdin", "not json"), ("empty stdin", "")):
        p = subprocess.run([sys.executable, "-I", GUARD], input=raw,
                           capture_output=True, text=True, cwd=ROOT)
        ok = p.returncode == 0 and not p.stdout.strip()
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28} -> exit {p.returncode}, "
              f"{len(p.stdout)} bytes")

    print(f"\n{'ALL PASS' if bad == 0 else str(bad) + ' FAILURES'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
