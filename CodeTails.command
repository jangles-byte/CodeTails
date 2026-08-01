#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  CodeTails — double-click me.
#  Starts the dashboard and opens it. Close this window (or ⌃C) to stop.
# ─────────────────────────────────────────────────────────────────────────────

cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
HERE="$(pwd)"

printf '\033]0;CodeTails\007'           # terminal window title
printf '\033[2J\033[H'                  # clear

# make sure the usual suspects are on PATH even in a bare login shell
export PATH="$HOME/.npm-global/bin:$HOME/.claude/local:/opt/homebrew/bin:/usr/local/bin:$PATH"

PY=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3)" /usr/bin/python3; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then PY="$candidate"; break; fi
done

if [ -z "$PY" ]; then
  echo ""
  echo "  CodeTails needs python3."
  echo "  Install Apple's command line tools with:  xcode-select --install"
  echo ""
  read -r -p "  press return to close "
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo ""
  echo "  Couldn't find the 'claude' CLI on PATH."
  echo "  Install it with:  npm install -g @anthropic-ai/claude-code"
  echo ""
  read -r -p "  press return to close "
  exit 1
fi

PYTHONUNBUFFERED=1 "$PY" -u "$HERE/server.py" "$@"
STATUS=$?

if [ $STATUS -ne 0 ] && [ $STATUS -ne 130 ]; then
  echo ""
  echo "  CodeTails stopped with status $STATUS."
  read -r -p "  press return to close "
fi
