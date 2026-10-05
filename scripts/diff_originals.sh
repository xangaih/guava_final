#!/usr/bin/env bash
# Writes a unified diff of each original against its descendant into diffs/.
# Run it any time; files that don't exist yet are skipped.
set -u
cd "$(dirname "$0")/.."
mkdir -p diffs
pair() {  # original  descendant  output
  if [ -f "$2" ]; then
    diff -u "$1" "$2" > "$3" || true
    echo "wrote $3 ($(wc -l < "$3" | tr -d ' ') lines)"
  else
    echo "skipped: $2 does not exist yet"
  fi
}
pair originals/fnol/__main__.py          mercury_claims/fnol_flow.py    diffs/fnol.diff
pair originals/claims_status/__main__.py mercury_claims/status_flow.py  diffs/claims_status.diff
