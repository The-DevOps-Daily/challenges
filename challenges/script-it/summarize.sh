#!/usr/bin/env bash
set -o pipefail

top=5
usage() {
  printf 'Usage: %s [-n N] [LOGFILE]\n' "${0##*/}" >&2
  exit 2
}

while getopts ':n:' opt; do
  case $opt in
    n) top=$OPTARG ;;
    \?) usage ;;
    :) usage ;;
  esac
done
shift $((OPTIND - 1))

[[ $top =~ ^[0-9]+$ && $top =~ [1-9] ]] || usage
(( $# <= 1 )) || usage

input=()
if (( $# == 1 )) && [[ $1 != - ]]; then
  if [[ ! -f "$1" || ! -r "$1" ]]; then
    printf '%s: cannot read log file: %s\n' "${0##*/}" "$1" >&2
    exit 1
  fi
  input=("$1")
  [[ $1 == /* ]] || input=("./$1")
fi

LC_ALL=C awk '
  {
    if ($0 == "") {
      next
    }
    if ($0 !~ /^[^ ]+ [^ ]+ [^ ]+ \[[^]]*\] "[A-Z]+ [^ "]+ [^ "]+" [2-5][0-9][0-9]( |$)/) {
      skipped++
      next
    }

    ip = $0
    sub(/ .*/, "", ip)
    request = $0
    sub(/^[^ ]+ [^ ]+ [^ ]+ \[[^]]*\] "/, "", request)
    quote = index(request, "\"")
    split(substr(request, 1, quote - 1), fields, / /)
    path = fields[2]
    sub(/\?.*/, "", path)
    after_request = substr(request, quote + 1)

    requests++
    if (!(ip in ips)) {
      unique_ips++
    }
    ips[ip]++
    paths[path]++
    statuses[substr(after_request, 2, 1)]++
  }
  END {
    printf "00\trequests: %d\n", requests
    printf "01\tunique_ips: %d\n", unique_ips
    printf "02\tstatus_2xx: %d\n", statuses[2]
    printf "03\tstatus_3xx: %d\n", statuses[3]
    printf "04\tstatus_4xx: %d\n", statuses[4]
    printf "05\tstatus_5xx: %d\n", statuses[5]
    printf "06\tskipped: %d\n", skipped
    printf "07\ttop_ips:\n"
    for (ip in ips) {
      printf "08\t%d\t%s\n", ips[ip], ip
    }
    printf "09\ttop_paths:\n"
    for (path in paths) {
      printf "10\t%d\t%s\n", paths[path], path
    }
  }
' "${input[@]}" |
  LC_ALL=C sort -t $'\t' -k1,1n -k2,2nr -k3 |
  LC_ALL=C awk -F '\t' -v top="$top" '
    $1 == "08" || $1 == "10" {
      if (++shown[$1] <= top) {
        first_tab = index($0, "\t")
        rest = substr($0, first_tab + 1)
        second_tab = index(rest, "\t")
        printf "%s %s\n", substr(rest, 1, second_tab - 1), substr(rest, second_tab + 1)
      }
      next
    }
    {
      print substr($0, index($0, "\t") + 1)
    }
  '
