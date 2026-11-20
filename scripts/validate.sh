#!/usr/bin/env bash
# Render every Kustomize root and validate it offline, strictly.
# Only local schemas are used, so a kind without a schema is an error.
set -euo pipefail
cd "$(dirname "$0")/.."

BIN=.cache/bin
LOCATION=".cache/schemas/{{ .Group }}/{{ .ResourceKind }}_{{ .ResourceAPIVersion }}.json"
ROOTS=(infra apps/hello/overlays/dev apps/hello/overlays/prod argocd argocd/apps)

for root in "${ROOTS[@]}"; do
  echo "== $root"
  "$BIN/kustomize" build "$root" > .cache/rendered.yaml
  "$BIN/kubeconform" -strict -summary -schema-location "$LOCATION" .cache/rendered.yaml
done

# Each negative case must be rejected, with the message named on its first line.
for f in tests/negative/*.yaml; do
  expect=$(sed -n '1s/^# expect: //p' "$f")
  if [[ -z $expect ]]; then
    echo "$f: first line must be '# expect: <text>'" >&2
    exit 1
  fi
  if "$BIN/kubeconform" -strict -schema-location "$LOCATION" "$f" > .cache/negative.out 2>&1; then
    echo "$f: expected validation to fail, but it passed" >&2
    exit 1
  fi
  if ! grep -qF -- "$expect" .cache/negative.out; then
    echo "$f: failed, but not with '$expect':" >&2
    cat .cache/negative.out >&2
    exit 1
  fi
  echo "rejected as expected: $f"
done
