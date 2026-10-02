#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write) for What's Next - runs after every file edit, by any agent.

- *.py: syntax check (python3 -m py_compile) on the edited file, then the fast test modules
  (~5s - everything except test_web and test_web_async, which take ~50s; run those via the
  testing-recipe skill before reporting).
- static/*: tests.test_ui (~1s), which checks the static files are linked, served and self-contained.
- Anything else (docs, specs, .claude/): nothing.
On failure it exits 2, so the output goes back to the agent to fix. Passing is silent.
TODO lint/type check: no ruff/pyflakes/mypy on this machine (no pip). Add here if installed."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SLOW = {"test_web", "test_web_async"}


def run(cmd):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout + r.stderr).strip()


def fail(what, output):
    tail = "\n".join(output.splitlines()[-30:])
    print(f"after-edit check failed ({what}):\n{tail}", file=sys.stderr)
    return 2


def main():
    try:
        file_path = (json.load(sys.stdin).get("tool_input") or {}).get("file_path") or ""
    except ValueError:
        return 0
    path = os.path.abspath(file_path)
    if not path.startswith(ROOT + os.sep):
        return 0
    rel = os.path.relpath(path, ROOT)
    if rel.endswith(".py"):
        code, out = run([sys.executable, "-m", "py_compile", rel])
        if code:
            return fail(f"syntax: {rel}", out)
        fast = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "tests"))
                      if f.startswith("test_") and f.endswith(".py") and f[:-3] not in SLOW)
        code, out = run([sys.executable, "-m", "unittest", "-q"] + [f"tests.{m}" for m in fast])
        return fail("fast tests", out) if code else 0
    if rel.startswith("static" + os.sep):
        code, out = run([sys.executable, "-m", "unittest", "-q", "tests.test_ui"])
        return fail("tests.test_ui", out) if code else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
