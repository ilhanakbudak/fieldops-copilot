#!/usr/bin/env bash
# Runs exactly what CI runs, failing on the first problem.
# pipefail matters: piping a step into `tail` would hide its exit status.
set -euo pipefail

run() { printf '=== %s ===\n' "$1"; shift; "$@"; }

# CI installs every extra, and so must this. PyMuPDF and PaddleOCR are optional
# at runtime and not optional to the type checker: with them absent, mypy treats
# their imports as `Any` and finds nothing to complain about. That is how a
# `no-untyped-call` in the PyMuPDF path passed here and failed in CI.
run "api deps"       uv sync --all-extras --dev --project services/api

run "shared build"   npm run build --workspace packages/shared
run "web typecheck"  npm run typecheck --workspace apps/web
run "web lint"       npm run lint --workspace apps/web
run "web test"       npm run test --workspace apps/web
run "web build"      npm run build --workspace apps/web
run "api lint"       npm run api:lint
run "api typecheck"  npm run api:typecheck
run "api test"       npm run api:test

printf '\nAll checks passed.\n'
