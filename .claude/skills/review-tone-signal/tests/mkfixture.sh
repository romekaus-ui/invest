#!/bin/bash
# Build a throwaway copy of the project so the orchestrator's gate and refusal
# paths can be exercised without touching the real repo. $1 = destination.
#
# Only committed files are copied: a fixture built from a dirty tree tests
# whatever happened to be lying around, which is not a test.
set -euo pipefail
SRC="$(git rev-parse --show-toplevel)"
DEST="$1"
rm -rf "$DEST"
mkdir -p "$DEST"
cd "$SRC"
tar -cf - $(git ls-files) | (cd "$DEST" && tar -xf -)
cd "$DEST"
git init -q .
git config user.email fixture@example.invalid
git config user.name fixture
git add -A >/dev/null
git commit -qm "fixture base"
