#!/usr/bin/env bash
#
# summarize.sh: print a short report for a web server access log.
#
# Usage: ./summarize.sh [-n N] [LOGFILE]
set -euo pipefail

usage() {
  echo "usage: summarize.sh [-n N] [LOGFILE]" >&2
  exit 2
}

top=5
while getopts ":n:" opt; do
  case $opt in
    n) top=$OPTARG ;;
    *) usage ;;
  esac
done
shift $((OPTIND - 1))

[[ $top =~ ^[1-9][0-9]*$ ]] || usage
(($# <= 1)) || usage

file=${1:--}
if [[ $file != - && ! (-f $file && -r $file) ]]; then
  echo "summarize.sh: cannot read $file" >&2
  exit 1
fi

export LC_ALL=C
awk -v top="$top" '
  $0 == "" { next }
  !/^[^ ]+ [^ ]+ [^ ]+ \[[^]]*\] "[A-Z]+ [^ "]+ [^ "]+" [2-5][0-9][0-9]( |$)/ { skipped++; next }
  {
    requests++
    ip = $1
    split($0, part, "\"")
    split(part[2], req, " ")
    path = req[2]
    sub(/\?.*/, "", path)
    status = substr(part[3], 2, 1)
    classes[status]++
    if (!(ip in ips)) unique++
    ips[ip]++
    paths[path]++
  }
  END {
    sorter = "sort -k1,1nr -k2,2 | head -n " top
    printf "requests: %d\n", requests
    printf "unique_ips: %d\n", unique
    for (c = 2; c <= 5; c++) printf "status_%dxx: %d\n", c, classes[c]
    printf "skipped: %d\n", skipped
    print "top_ips:"
    fflush()
    for (ip in ips) print ips[ip], ip | sorter
    close(sorter)
    print "top_paths:"
    fflush()
    for (p in paths) print paths[p], p | sorter
    close(sorter)
  }
' "$file"
