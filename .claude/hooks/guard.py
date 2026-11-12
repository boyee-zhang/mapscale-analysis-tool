#!/usr/bin/env python3
"""PreToolUse guard: the repo's hard rules, enforced by the harness rather than by a comment.

Every rule here is either documented in CLAUDE.md as a "do not", or is something that actually
happened in this repo and was not caught. Prompts and comments are probabilistic; this is not.

Contract (see the hook docs): print a JSON object with
`hookSpecificOutput.permissionDecision` = deny | ask to stop or question a call. **When no rule
matches, print NOTHING and exit 0** — emitting "allow" would bypass the normal permission flow
and turn this guard into a back door.
"""
from __future__ import annotations

import json
import os
import re
import sys

RULES_DOC = "CLAUDE.md"


def out(decision: str, reason: str) -> None:
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}, sys.stdout)
    sys.stdout.write("\n")
    sys.exit(0)


# ── Bash ──────────────────────────────────────────────────────────────────────

# A command only counts when it sits in COMMAND POSITION: at the start, or after a separator.
# Without this, `grep -rn \'git push --force\' docs/` is blocked -- a command that merely
# CONTAINS the text is not a command that runs it, and that false positive showed up on the
# first test pass.
# A newline separates commands too. Without it `^` only matched the very start of the
# command string, so in a multi-line command every line after the first was invisible --
# which silently made the "a heredoc that executes its stdin stays under the rules"
# exception do nothing at all.
_POS = r"(?:^|[;&|\n]|\b(?:then|do|else)\b)\s*(?:sudo\s+|env\s+\S+=\S+\s+|time\s+)*"

# `git push origin --delete a b c` deleted 13 remote branches in one call with nothing asking.
# `:branch` is the old spelling of the same thing.
_PUSH_DELETE = re.compile(_POS + r"git\s+push\b[^|;&]*?(--delete\b|\s:\S)")
_PUSH_FORCE = re.compile(_POS + r"git\s+push\b[^|;&]*?(--force(?!-with-lease)\b|\s-f\b)")
# CLAUDE.md: "-v is the command that actually costs 40 minutes of graph rebuilding."
_COMPOSE_DOWN_V = re.compile(_POS + r"docker\s+compose\b[^|;&]*\bdown\b[^|;&]*(\s-v\b|--volumes\b)")
_HARD_RESET = re.compile(_POS + r"git\s+(reset\s+--hard|clean\s+-[a-z]*f)")


# A heredoc body is DATA, not commands. Found the hard way: this guard blocked its own commit,
# whose message quoted a blocked command as an example, because the body is part of the Bash
# tool's `command` string. The exception is a command that EXECUTES its stdin (bash, sh, zsh,
# eval) -- there the body really is commands, so it stays under the rules.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1(.*?)^\2$", re.S | re.M)
_RUNS_STDIN = re.compile(r"(?:^|[;&|]|\s)(?:ba|z|k|da)?sh\b(?![\w-])|(?:^|[;&|]|\s)eval\b")


def strip_heredocs(cmd: str) -> str:
    """Blank out heredoc bodies so prose inside them is not read as commands."""
    if _RUNS_STDIN.search(cmd.split("<<")[0]):
        return cmd
    prev = None
    while prev != cmd:
        prev = cmd
        cmd = _HEREDOC.sub(lambda m: "<<" + m.group(2) + "\n" + m.group(2), cmd, count=1)
    return cmd


def check_bash(cmd: str) -> None:
    cmd = strip_heredocs(cmd)
    if _PUSH_DELETE.search(cmd):
        out("ask", "Deletes a remote branch — irreversible for anyone who has not fetched it, "
                   "and it closes any open PR whose head it is. 13 branches were deleted this "
                   "way in one call with nothing asking. Confirm the branch list first.")
    if _PUSH_FORCE.search(cmd):
        out("deny", "force-push rewrites published history. Use --force-with-lease, or push a "
                    "new commit.")
    if _COMPOSE_DOWN_V.search(cmd):
        out("ask", f"`down -v` removes the volumes. {RULES_DOC}: that is the command that "
                   f"actually costs 40 minutes of ORS graph rebuilding, and the seven volumes "
                   f"otherwise survive a plain `down`.")
    if _HARD_RESET.search(cmd):
        out("ask", "Discards uncommitted work irreversibly.")


# ── Write / Edit ──────────────────────────────────────────────────────────────

_MIGRATION = re.compile(r"(?:^|/)migrations/\d{4}_[^/]+\.sql$")
# The one module that is allowed to construct a client — it IS the shared client.
_CLIENT_MODULE = re.compile(r"(?:^|/)api/main/http_client\.py$")
_API_PY = re.compile(r"(?:^|/)api/main/.+\.py$")
_DB_PY = re.compile(r"(?:^|/)api/main/db\.py$")


def check_write(path: str, text: str, is_edit: bool) -> None:
    # Matched on the path TAIL: an absolute path and a relative one behave identically, so
    # this guard never depends on the cwd it happens to be spawned in. Measured: with the
    # old relpath form, the same absolute path denied from the repo root and passed from
    # /tmp.
    rel = path.replace(os.sep, "/")

    # CLAUDE.md: migrations are "applied forward-only by a hand-written runner; there is no
    # `down`". Editing one that has already been applied silently diverges every database.
    if _MIGRATION.search(rel) and os.path.exists(path):
        out("deny", f"{rel} already exists and migrations are forward-only ({RULES_DOC}): there "
                    f"is no down migration, so editing an applied file leaves every database "
                    f"that ran it in a state no file describes. Add a new NNNN_*.sql instead.")

    # CLAUDE.md, measured: constructing a client per call costs 13.88 ms of CPU building its TLS
    # context — 22.3 -> 7.5 ms per request, 40.9 -> 119.4 req/s when it was fixed.
    if _API_PY.search(rel) and not _CLIENT_MODULE.search(rel):
        if "httpx.AsyncClient(" in text:
            out("deny", f"{rel} constructs an httpx.AsyncClient. {RULES_DOC}: use the shared "
                        f"client in api/main/http_client.py, never a new one in a request path "
                        f"— measured 22.3 -> 7.5 ms CPU per request and 40.9 -> 119.4 req/s.")

    # CLAUDE.md: "db.py sets plan_cache_mode=force_custom_plan ... and that is load-bearing."
    # Measured: the same query took 2.4-3.1 s five times and 104 s on the sixth without it.
    if _DB_PY.search(rel) and is_edit and "plan_cache_mode" not in text:
        try:
            had = "plan_cache_mode" in open(path, encoding="utf-8").read()
        except OSError:
            had = False
        if had:
            out("ask", f"This edit to db.py drops plan_cache_mode. {RULES_DOC} calls it "
                       f"load-bearing: without it the same spatial query took 2.4-3.1 s for five "
                       f"executions and 104 s on the sixth, because PostgreSQL switches to a "
                       f"generic plan and the search shape arrives as a parameter.")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0                      # malformed input must never block a tool call
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}
    try:
        if tool == "Bash":
            check_bash(ti.get("command", "") or "")
        elif tool in ("Write", "Edit"):
            path = ti.get("file_path", "") or ""
            text = (ti.get("content") or "") + (ti.get("new_string") or "")
            check_write(path, text, is_edit=(tool == "Edit"))
    except SystemExit:
        raise
    except Exception as e:
        # Fail OPEN (a bug here must not wedge the session) but never fail SILENTLY: a guard
        # that quietly stops enforcing is worse than no guard, because the rules look live.
        json.dump({"systemMessage":
                   f"guard.py did not run for this {tool} call "
                   f"({type(e).__name__}: {e}) — the repo's hard-rule checks were skipped."},
                  sys.stdout)
        sys.stdout.write("\n")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
