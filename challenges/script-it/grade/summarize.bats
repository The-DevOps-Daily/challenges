#!/usr/bin/env bats
# Tests for summarize.sh. Every test name starts with the id of the check it
# belongs to in challenge.json. run.sh runs the script under test in a
# throwaway container, so the script cannot touch these tests or the runner.

bats_require_minimum_version 1.5.0

setup_file() {
  export DATA_DIR="$BATS_FILE_TMPDIR/data"
  mkdir -p "$DATA_DIR"
  cp "$BATS_TEST_DIRNAME"/fixtures/*.log "$DATA_DIR/"
  cp "$DATA_DIR/basic.log" "$DATA_DIR/my access.log"
  cp "$DATA_DIR/basic.log" "$DATA_DIR/locked.log"
  chmod 000 "$DATA_DIR/locked.log"
  mkdir "$DATA_DIR/a-directory"

  local seed
  for i in 1 2 3; do
    seed=$(( (RANDOM << 15) | RANDOM ))
    python3 "$BATS_TEST_DIRNAME/gen_log.py" --seed "$seed" --lines $((i * 1500)) --top $((i + 2)) \
      --log "$DATA_DIR/random-$i.log" --expected "$DATA_DIR/random-$i.expected"
  done
  seed=$(( (RANDOM << 15) | RANDOM ))
  python3 "$BATS_TEST_DIRNAME/gen_log.py" --seed "$seed" --lines 100000 \
    --log "$DATA_DIR/big.log" --expected "$DATA_DIR/big.expected"
}

teardown_file() {
  chmod 644 "$DATA_DIR/locked.log" 2>/dev/null || true
}

solution() {
  "$BATS_TEST_DIRNAME/run.sh" "$@"
}

expected() {
  cat "$BATS_TEST_DIRNAME/expected/$1.txt"
}

# --- report -----------------------------------------------------------------

@test "[report] prints the report for a small log" {
  run --separate-stderr solution basic.log < /dev/null
  [ "$status" -eq 0 ]
  [ "$output" = "$(expected basic)" ]
}

# --- edge-cases -------------------------------------------------------------

@test "[edge-cases] skips malformed lines, ignores empty lines and strips query strings" {
  run --separate-stderr solution edge.log < /dev/null
  [ "$status" -eq 0 ]
  [ "$output" = "$(expected edge)" ]
}

@test "[edge-cases] counts the last line when the file has no trailing newline" {
  run --separate-stderr solution no-newline.log < /dev/null
  [ "$status" -eq 0 ]
  [ "$output" = "$(expected no-newline)" ]
}

@test "[edge-cases] prints an all-zero report for an empty log" {
  run --separate-stderr solution empty.log < /dev/null
  [ "$status" -eq 0 ]
  [ "$output" = "$(expected empty)" ]
}

@test "[edge-cases] prints an all-zero report for a log with only empty lines" {
  run --separate-stderr solution blank.log < /dev/null
  [ "$status" -eq 0 ]
  [ "$output" = "$(expected blank)" ]
}

# --- input ------------------------------------------------------------------

@test "[input] reads stdin when no file is given" {
  run --separate-stderr solution < "$DATA_DIR/basic.log"
  [ "$status" -eq 0 ]
  [ "$output" = "$(expected basic)" ]
}

@test "[input] reads stdin when the file is -" {
  run --separate-stderr solution - < "$DATA_DIR/basic.log"
  [ "$status" -eq 0 ]
  [ "$output" = "$(expected basic)" ]
}

@test "[input] reads a file whose name has a space in it" {
  run --separate-stderr solution "my access.log" < /dev/null
  [ "$status" -eq 0 ]
  [ "$output" = "$(expected basic)" ]
}

# --- arguments --------------------------------------------------------------

@test "[arguments] -n limits both top lists" {
  run --separate-stderr solution -n 2 basic.log < /dev/null
  [ "$status" -eq 0 ]
  [ "$output" = "$(expected basic-top2)" ]
}

@test "[arguments] -n 0 is a usage error (exit 2, message on stderr)" {
  run --separate-stderr solution -n 0 basic.log < /dev/null
  [ "$status" -eq 2 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}

@test "[arguments] -n with a word is a usage error (exit 2, message on stderr)" {
  run --separate-stderr solution -n many basic.log < /dev/null
  [ "$status" -eq 2 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}

@test "[arguments] an unknown option is a usage error (exit 2, message on stderr)" {
  run --separate-stderr solution -x basic.log < /dev/null
  [ "$status" -eq 2 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}

@test "[arguments] two log files are a usage error (exit 2, message on stderr)" {
  run --separate-stderr solution basic.log edge.log < /dev/null
  [ "$status" -eq 2 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}

# --- errors -----------------------------------------------------------------

@test "[errors] a missing file exits 1 with a message on stderr" {
  run --separate-stderr solution does-not-exist.log < /dev/null
  [ "$status" -eq 1 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}

@test "[errors] an unreadable file exits 1 with a message on stderr" {
  run --separate-stderr solution locked.log < /dev/null
  [ "$status" -eq 1 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}

@test "[errors] a directory exits 1 with a message on stderr" {
  run --separate-stderr solution a-directory < /dev/null
  [ "$status" -eq 1 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}

# --- random -----------------------------------------------------------------

@test "[random] gives the right report for a random log (1,500 lines, -n 3)" {
  run --separate-stderr solution -n 3 random-1.log < /dev/null
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$DATA_DIR/random-1.expected")" ]
}

@test "[random] gives the right report for a random log (3,000 lines, -n 4)" {
  run --separate-stderr solution -n 4 random-2.log < /dev/null
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$DATA_DIR/random-2.expected")" ]
}

@test "[random] gives the right report for a random log on stdin (4,500 lines, -n 5)" {
  run --separate-stderr solution -n 5 < "$DATA_DIR/random-3.log"
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$DATA_DIR/random-3.expected")" ]
}

# --- speed ------------------------------------------------------------------

@test "[speed] summarizes 100,000 lines correctly in under 10 seconds" {
  local start end
  start=$(date +%s%N)
  run --separate-stderr solution big.log < /dev/null
  end=$(date +%s%N)
  [ "$status" -eq 0 ]
  [ "$output" = "$(cat "$DATA_DIR/big.expected")" ]
  [ $(( (end - start) / 1000000 )) -lt 10000 ]
}
