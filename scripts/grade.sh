#!/usr/bin/env bash
# Run the checks for one challenge. The Grade workflow calls this after
# prepare.py has built the work directory from main plus the PR's solution
# files. Each challenge's grade/grade.sh records one line per check.
set -euo pipefail

: "${CHALLENGE:?}" "${RUNNER_TEMP:?}"
if [[ ! $CHALLENGE =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  echo "unexpected challenge id" >&2
  exit 1
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
grader="$root/challenges/$CHALLENGE/grade/grade.sh"
[[ -f $grader ]] || { echo "no grader for $CHALLENGE" >&2; exit 1; }

export GRADE_ROOT=$root
export GRADE_LIB="$root/scripts/grade-lib.sh"
export GRADE_DIR="$root/challenges/$CHALLENGE/grade"
export GRADE_OUT="$RUNNER_TEMP/grade"
export GRADE_WORK="$GRADE_OUT/work"
export GRADE_RESULTS="$GRADE_OUT/checks.tsv"
export GRADE_TOOLS="$RUNNER_TEMP/grade-tools"
mkdir -p "$GRADE_TOOLS"
: > "$GRADE_RESULTS"

bash "$grader"
touch "$GRADE_OUT/done"
