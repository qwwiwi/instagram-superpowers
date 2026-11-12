#!/usr/bin/env bash
# Local checks of the skill: syntax of every script and the offline test suite.
# Usage: reels-ffmpeg-montage/scripts/check.sh      (PYTHON=python3.12 ... to pick an interpreter)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill="$(dirname "$here")"

pick_python() {
  local candidate
  for candidate in "${PYTHON:-}" python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c "import pytest" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if ! py="$(pick_python)"; then
  echo "check.sh: no Python with pytest found. Install it (python3 -m pip install pytest) or set PYTHON=<interpreter>." >&2
  exit 2
fi
echo "check.sh: using $("$py" --version) ($py)"

echo "check.sh: syntax"
for script in "$skill"/scripts/*.py; do
  "$py" -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1])' "$script"
done

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "check.sh: ffmpeg/ffprobe not found, the end-to-end tests will be skipped" >&2
fi

echo "check.sh: tests"
"$py" -m pytest -q -rs -p no:cacheprovider "$skill/tests"
echo "check.sh: OK"
