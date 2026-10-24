#!/usr/bin/env bash
# Grader for script-it: ShellCheck, then the bats tests in summarize.bats.
set -uo pipefail
# shellcheck source-path=SCRIPTDIR/../../..
# shellcheck source=scripts/grade-lib.sh
source "$GRADE_LIB"

SHELLCHECK_VERSION=0.11.0
SHELLCHECK_SHA256=8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198
BATS_VERSION=1.14.0
BATS_COMMIT=eb7f42f8d608ac693d7a4b67474f6714ea68cfc5
export SANDBOX_IMAGE=ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac
export SOLUTION_DIR=$GRADE_WORK

script="$GRADE_WORK/summarize.sh"
test_checks=(report edge-cases input arguments errors random speed)

if [[ ! -f $script ]]; then
  record shellcheck fail "summarize.sh is missing."
  for check in "${test_checks[@]}"; do record "$check" skip "summarize.sh is missing."; done
  exit 0
fi

# Pinned tools. Nothing from the PR runs on the runner itself: ShellCheck
# only reads the script, and the tests run it in a container (see run.sh).
# Local runs can put the tools in $GRADE_TOOLS first; CI starts empty.
if [[ ! -x $GRADE_TOOLS/shellcheck ]]; then
  tarball="$GRADE_TOOLS/shellcheck.tar.xz"
  fetch "https://github.com/koalaman/shellcheck/releases/download/v$SHELLCHECK_VERSION/shellcheck-v$SHELLCHECK_VERSION.linux.x86_64.tar.xz" \
    "$SHELLCHECK_SHA256" "$tarball" || exit 1
  tar -xJf "$tarball" -C "$GRADE_TOOLS" --strip-components=1 "shellcheck-v$SHELLCHECK_VERSION/shellcheck" || exit 1
fi
if [[ ! -x $GRADE_TOOLS/bats/bin/bats ]]; then
  git clone --quiet --depth 1 --branch "v$BATS_VERSION" https://github.com/bats-core/bats-core.git "$GRADE_TOOLS/bats" || exit 1
  [[ $(git -C "$GRADE_TOOLS/bats" rev-parse HEAD) == "$BATS_COMMIT" ]] || { echo "unexpected bats commit" >&2; exit 1; }
fi
if [[ ${SANDBOX:-docker} != none ]]; then
  timeout 300 docker pull --quiet "$SANDBOX_IMAGE" || exit 1
fi

# --- shellcheck ---------------------------------------------------------------
if grep -Eiq '^[[:space:]]*#[[:space:]]*shellcheck[[:space:]]+(.*[[:space:]])?disable=' "$script"; then
  record shellcheck fail "The script switches ShellCheck checks off with a disable directive. Fix the code instead."
else
  sc_out=$("$GRADE_TOOLS/shellcheck" --norc --severity=style --format=gcc "$script" 2>&1)
  sc_status=$?
  if [[ $sc_status -eq 0 ]]; then
    record shellcheck pass "No issues."
  else
    issues=$(grep -cE '^[^:]+:[0-9]+:[0-9]+: ' <<<"$sc_out")
    list=$(grep -oE '^[^:]+:[0-9]+:[0-9]+: .*\[SC[0-9]+\]$' <<<"$sc_out" |
      sed -E 's/^[^:]+:([0-9]+):[0-9]+: .*\[(SC[0-9]+)\]$/line \1 \2/' | head -n 8 | paste -sd, - | sed 's/,/, /g')
    record shellcheck fail "ShellCheck reports $issues issues: $list."
  fi
fi

# --- bats tests -----------------------------------------------------------------
tap="$GRADE_OUT/bats.tap"
timeout 900 "$GRADE_TOOLS/bats/bin/bats" --formatter tap "$GRADE_DIR/summarize.bats" > "$tap" 2>&1
echo "bats exit code: $?"
print_untrusted "$tap"

for check in "${test_checks[@]}"; do
  total=0
  failed=()
  while IFS= read -r line; do
    if [[ $line =~ ^(ok|not\ ok)\ [0-9]+\ \[([a-z-]+)\]\ (.*)$ ]] && [[ ${BASH_REMATCH[2]} == "$check" ]]; then
      total=$((total + 1))
      [[ ${BASH_REMATCH[1]} == ok ]] || failed+=("${BASH_REMATCH[3]}")
    fi
  done < "$tap"
  if ((total == 0)); then
    record "$check" fail "The tests for this check did not run."
  elif ((${#failed[@]} == 0)); then
    record "$check" pass "$total of $total tests passed."
  else
    names=$(printf '%s; ' "${failed[@]}")
    record "$check" fail "${#failed[@]} of $total tests failed: ${names%; }."
  fi
done
