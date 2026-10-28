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
  # Details can quote the submission; break up "::" so the runner never reads
  # a workflow command in the log line.
  printf '%-4s  %s  %s\n' "$status" "$id" "${detail//::/: :}"
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

# go_build SRC_DIR OUT
# Builds a grader helper written in Go (from main, before any submission code
# runs) with a Go toolchain pinned by checksum. go.sum pins every module.
GO_VERSION=1.26.10
GO_SHA256=ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5
go_build() {
  local src=$1 out=$2 go="$GRADE_TOOLS/go/bin/go"
  if [[ ! -x $go ]]; then
    fetch "https://go.dev/dl/go$GO_VERSION.linux-amd64.tar.gz" "$GO_SHA256" "$GRADE_TOOLS/go.tar.gz" || return 1
    tar -xzf "$GRADE_TOOLS/go.tar.gz" -C "$GRADE_TOOLS" || return 1
    rm -f "$GRADE_TOOLS/go.tar.gz"
  fi
  (cd "$src" && env GOTOOLCHAIN=local GOFLAGS=-mod=readonly GOWORK=off CGO_ENABLED=0 \
    GOPATH="$GRADE_TOOLS/gopath" GOCACHE="$GRADE_TOOLS/gocache" \
    GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org \
    timeout 600 "$go" build -trimpath -o "$out" .)
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
