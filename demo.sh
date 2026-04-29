#!/usr/bin/env bash
# Bastion — one-shot demo launcher.
#
# Two-step usage on a fresh clone:
#   1) cd to this directory
#   2) ./demo.sh
#
# Optional argument picks a policy profile or switches mode:
#   ./demo.sh                    # cred-safety with default policy (the headline)
#   ./demo.sh prod-restricted    # same plan, prod-restricted policy
#   ./demo.sh gov-airgapped      # same plan, air-gapped policy (every step blocked)
#   ./demo.sh audit              # pretty-print the most recent audit log
#
# The script:
#   - finds a Python 3.11+ interpreter (PATH, then standard Windows locations)
#   - sets PYTHONIOENCODING=utf-8 so rich's framed output renders on Windows
#   - installs the package in editable mode on first run
#   - dispatches the requested demo command

set -euo pipefail

# ---------------------------------------------------------------------------
# Find a Python interpreter.
# ---------------------------------------------------------------------------
find_python() {
  for cmd in python python3 py; do
    if command -v "$cmd" >/dev/null 2>&1; then
      echo "$cmd"
      return 0
    fi
  done
  # Common Windows install paths under Git Bash.
  for candidate in \
    "${LOCALAPPDATA:-}/Programs/Python/Python313/python.exe" \
    "${LOCALAPPDATA:-}/Programs/Python/Python312/python.exe" \
    "${LOCALAPPDATA:-}/Programs/Python/Python311/python.exe" \
    "/c/Python313/python.exe" \
    "/c/Python312/python.exe" \
    "/c/Python311/python.exe"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

PY="$(find_python)" || {
  echo "ERROR: Python 3.11+ not found." >&2
  echo "Install from https://www.python.org/ and re-run." >&2
  exit 1
}

# ---------------------------------------------------------------------------
# Windows: rich needs UTF-8 to render the framed output cleanly.
# ---------------------------------------------------------------------------
export PYTHONIOENCODING=utf-8

# ---------------------------------------------------------------------------
# First-run install (skipped if the package is already importable).
# ---------------------------------------------------------------------------
if ! "$PY" -c "import claude_demo" 2>/dev/null; then
  echo ">>> First run: installing claude-demo + dependencies (rich, PyYAML)..."
  "$PY" -m pip install -e . --quiet
  echo ">>> Install complete."
  echo
fi

# ---------------------------------------------------------------------------
# Dispatch.
# ---------------------------------------------------------------------------
CMD="${1:-default}"

case "$CMD" in
  audit)
    LATEST=$(ls -t runs/*.jsonl 2>/dev/null | head -1 || true)
    if [ -z "${LATEST:-}" ]; then
      echo "No audit logs in runs/ yet. Run ./demo.sh first." >&2
      exit 1
    fi
    echo ">>> Viewing $LATEST"
    "$PY" -m claude_demo audit view "$LATEST"
    ;;

  default | cred-safety)
    "$PY" -m claude_demo run cred-safety
    ;;

  prod-restricted | gov-airgapped)
    "$PY" -m claude_demo run cred-safety --policy "$CMD"
    ;;

  openai)
    if [ -z "${OPENAI_API_KEY:-}" ]; then
      echo "ERROR: OPENAI_API_KEY is not set." >&2
      echo "  export OPENAI_API_KEY=sk-..." >&2
      exit 1
    fi
    if ! "$PY" -c "import openai" 2>/dev/null; then
      echo ">>> Installing openai SDK (one-time)..."
      "$PY" -m pip install -e ".[openai]" --quiet
    fi
    "$PY" -m examples.optional_openai_demo
    ;;

  redteam)
    "$PY" -m claude_demo redteam
    ;;

  -h | --help | help)
    cat <<'USAGE'
Bastion demo launcher.

Usage:
  ./demo.sh                    Run the cred-safety demo with the default policy
  ./demo.sh prod-restricted    Same demo, prod-restricted policy
  ./demo.sh gov-airgapped      Same demo, air-gapped policy (every step blocked)
  ./demo.sh openai             OpenAI-driven demo with prompt-injection test
                                 (requires OPENAI_API_KEY)
  ./demo.sh redteam            Fire 20+ adversarial scenarios at the runtime
  ./demo.sh audit              Pretty-print the most recent audit log
  ./demo.sh tests              Run the full unit test suite
USAGE
    ;;

  tests)
    "$PY" -m unittest discover tests
    ;;

  *)
    echo "Unknown command: $CMD" >&2
    echo "Run './demo.sh --help' for usage." >&2
    exit 2
    ;;
esac
