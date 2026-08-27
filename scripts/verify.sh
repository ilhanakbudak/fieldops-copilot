#!/usr/bin/env bash
# Runs exactly what CI runs, failing on the first problem.
# pipefail matters: piping a step into `tail` would hide its exit status.
set -euo pipefail

run() { printf '=== %s ===\n' "$1"; shift; "$@"; }

run "shared build"   npm run build --workspace packages/shared
run "web typecheck"  npm run typecheck --workspace apps/web
run "web lint"       npm run lint --workspace apps/web
run "web build"      npm run build --workspace apps/web
run "api lint"       npm run api:lint
run "api typecheck"  npm run api:typecheck
run "api test"       npm run api:test

printf '\nAll checks passed.\n'
