#!/usr/bin/env bash
# Grader for write-the-alerts: check the shape of alerts.yml, load it with
# promtool, then run promtool unit tests with random series for each alert.
set -uo pipefail
# shellcheck source-path=SCRIPTDIR/../../..
# shellcheck source=scripts/grade-lib.sh
source "$GRADE_LIB"

PROMETHEUS_IMAGE=prom/prometheus:v3.15.0@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e
rules="$GRADE_WORK/alerts.yml"
scenario_checks=(instance-down targets-missing error-rate slow-requests)

skip() {
  local reason=$1
  shift
  for check in "$@"; do record "$check" skip "$reason"; done
}

if [[ ! -f $rules ]]; then
  record rules-load fail "alerts.yml is missing."
  skip "Skipped: there is no alerts.yml." "${scenario_checks[@]}"
  exit 0
fi

# promtool runs in a container with no network, read-only files and an
# unprivileged user. It only reads alerts.yml (as data: PromQL and templates
# cannot run programs or read files) and the test files written by the grader.
promtool() {
  local tests=$1
  shift
  timeout 120 docker run --rm \
    --network none \
    --read-only \
    --user 65534:65534 \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --memory 512m \
    --pids-limit 64 \
    --cpus 1 \
    -v "$rules:/rules/alerts.yml:ro" \
    -v "$tests:/tests:ro" \
    --entrypoint /bin/promtool \
    "$PROMETHEUS_IMAGE" "$@"
}

timeout 300 docker pull --quiet "$PROMETHEUS_IMAGE" > /dev/null || exit 1
if [[ ! -x $GRADE_TOOLS/venv/bin/python ]]; then
  python3 -m venv "$GRADE_TOOLS/venv" || exit 1
  "$GRADE_TOOLS/venv/bin/pip" install --quiet --disable-pip-version-check --no-deps --only-binary :all: \
    --require-hashes -r "$GRADE_DIR/requirements-grader.txt" || exit 1
fi

# --- rules-load -------------------------------------------------------------------
shape=$("$GRADE_TOOLS/venv/bin/python" -I "$GRADE_DIR/check_rules.py" "$rules")
shape_status=$?
if ((shape_status > 2)); then
  exit 1
fi
empty="$GRADE_OUT/empty"
mkdir -p "$empty"
promtool "$empty" check rules /rules/alerts.yml > "$GRADE_OUT/check-rules.log" 2>&1
check_status=$?
echo "::group::promtool check rules"
print_untrusted "$GRADE_OUT/check-rules.log"
echo "::endgroup::"

if ((shape_status == 2)); then
  record rules-load fail "$(head -n 1 <<<"$shape")"
  skip "Skipped: alerts.yml did not load." "${scenario_checks[@]}"
  exit 0
fi
if ((check_status != 0)); then
  first=$(grep -m1 -iE 'error|FAILED' "$GRADE_OUT/check-rules.log" | cut -c1-200)
  record rules-load fail "promtool check rules failed. ${first:-See the log in the workflow run.}"
  skip "Skipped: alerts.yml did not load." "${scenario_checks[@]}"
  exit 0
fi
if ((shape_status == 1)); then
  record rules-load fail "$(head -n 3 <<<"$shape" | paste -sd ' ' -)"
else
  record rules-load pass "promtool loads the rules, and the four alerts have their severity, summary and runbook_url."
fi

# --- scenarios ---------------------------------------------------------------------
# New random series for every run. promtool reads the test files; the rules
# never see them. One file per scenario, so promtool's exit code is the result.
tests="$GRADE_OUT/alert-tests"
python3 -I "$GRADE_DIR/gen_tests.py" --out "$tests" --rules /rules/alerts.yml || exit 1
results="$GRADE_OUT/alert-results.tsv"
: > "$results"
while IFS=$'\t' read -r file check description; do
  [[ $file =~ ^[a-z-]+-[0-9]+\.yml$ ]] || exit 1
  promtool "$tests" test rules "/tests/$file" > "$GRADE_OUT/$file.log" 2>&1
  printf '%s\t%s\t%s\n' "$check" "$?" "$description" >> "$results"
  echo "::group::$file: $description"
  print_untrusted "$GRADE_OUT/$file.log"
  echo "::endgroup::"
done < <(python3 -I -c '
import json, sys
for s in json.load(open(sys.argv[1])):
    print(s["file"], s["check"], s["description"], sep="\t")
' "$tests/scenarios.json")

for check in "${scenario_checks[@]}"; do
  total=0
  failed=()
  while IFS=$'\t' read -r scheck status description; do
    [[ $scheck == "$check" ]] || continue
    total=$((total + 1))
    [[ $status == 0 ]] || failed+=("$description")
  done < "$results"
  if ((total == 0)); then
    record "$check" fail "The scenarios for this check did not run."
  elif ((${#failed[@]} == 0)); then
    record "$check" pass "$total of $total scenarios with random series passed."
  else
    record "$check" fail "${#failed[@]} of $total scenarios failed. First: ${failed[0]}."
  fi
done
