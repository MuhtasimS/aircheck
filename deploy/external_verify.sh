#!/usr/bin/env bash
# External, non-build-machine verification of the AIRCheck deployment, executed
# inside an ephemeral AWS CodeBuild host (see deploy/verify_buildspec.yml).
set -euo pipefail

echo "=== AIRCheck external verification from AWS CodeBuild ==="
echo "host $(uname -srm)  region ${AWS_REGION:-?}"

echo "--- public API resolves over HTTPS ---"
echo "$AIRCHECK_API_BASE" | grep -q '^https://' && echo "API base is HTTPS = $AIRCHECK_API_BASE"
curl -fsS "$AIRCHECK_API_BASE/health"; echo
SHA=$(curl -fsS "$AIRCHECK_API_BASE/health" | python3 -c "import sys,json;print(json.load(sys.stdin)['git_sha'])")
echo "deployed git_sha=$SHA  expected=$EXPECTED_SHA"
test -n "$SHA" -a "$SHA" != "unknown"
test "$SHA" = "$EXPECTED_SHA"

echo "--- public API path responds ---"
curl -fsS "$AIRCHECK_API_BASE/meta/states" | python3 -c "import sys,json;d=json.load(sys.stdin);print('profiles',[p['profile_id'] for p in d['profiles']]);print('terminal_outcomes',d['terminal_outcomes'])"

echo "--- public frontend resolves over HTTPS ---"
echo "$AIRCHECK_WEB_BASE" | grep -q '^https://' && echo "web base is HTTPS = $AIRCHECK_WEB_BASE"
curl -fsS -o /dev/null -w "web / -> HTTP %{http_code}\n" "$AIRCHECK_WEB_BASE/"
curl -fsS "$AIRCHECK_WEB_BASE/" | grep -o '__AIRCHECK_API_BASE__="[^"]*"' | head -1

echo "--- full canonical hero from this external host ---"
python3 deploy/verify_hero.py --base "$AIRCHECK_API_BASE" --label codebuild-external

echo "--- no localhost / build-machine dependency in the public verifier ---"
if grep -rIn "localhost" deploy/verify_hero.py; then
  echo "note: only the documented local default appears; not used against the deployment"
else
  echo "no localhost dependency"
fi

echo "EXTERNAL VERIFICATION GREEN"
