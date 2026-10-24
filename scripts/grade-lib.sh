# shellcheck shell=bash
# Helpers shared by the challenge graders in challenges/*/grade/grade.sh.

# record CHECK_ID pass|fail|skip [DETAIL]
# Appends one check result. DETAIL is shown in the PR comment, so keep it to
# what is wrong, not how to fix it.
record() {
  local id=$1 status=$2 detail=${3:-}
  detail=${detail//$'\t'/ }
  detail=${detail//$'\n'/ }
  printf '%s\t%s\t%s\n' "$id" "$status" "$detail" >> "$GRADE_RESULTS"
  printf '%-4s  %s  %s\n' "$status" "$id" "$detail"
}

# fetch URL SHA256 DEST
# Downloads a pinned tool and refuses it if the checksum does not match.
fetch() {
  local url=$1 sum=$2 dest=$3
  curl -fsSL --retry 3 --max-time 120 -o "$dest" "$url"
  echo "$sum  $dest" | sha256sum --check --status || {
    echo "checksum mismatch for $url" >&2
    rm -f "$dest"
    return 1
  }
}

# print_untrusted FILE
# Prints output that came from the submission (build logs, test output,
# container logs) with workflow commands switched off, so lines such as
# "::error::" in it are shown as text instead of being run by the runner.
print_untrusted() {
  local token
  token=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
  echo "::stop-commands::$token"
  cat "$1"
  echo "::$token::"
}
