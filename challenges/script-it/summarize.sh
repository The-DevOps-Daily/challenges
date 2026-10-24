#!/usr/bin/env bash
# Pure bash version: no awk, one while-read loop.
set -euo pipefail
usage() { echo "usage: summarize.sh [-n N] [LOGFILE]" >&2; exit 2; }
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
[[ $file == - ]] && file=/dev/stdin
re='^([^ ]+) [^ ]+ [^ ]+ \[[^]]*\] "[A-Z]+ ([^ "]+) [^ "]+" ([2-5])[0-9][0-9]( |$)'
declare -A ips=() paths=()
requests=0 skipped=0 c2=0 c3=0 c4=0 c5=0
while IFS= read -r line || [[ -n $line ]]; do
  [[ -z $line ]] && continue
  if [[ $line =~ $re ]]; then
    ((++requests))
    ip=${BASH_REMATCH[1]} path=${BASH_REMATCH[2]%%\?*}
    case ${BASH_REMATCH[3]} in 2) ((++c2)) ;; 3) ((++c3)) ;; 4) ((++c4)) ;; 5) ((++c5)) ;; esac
    ips[$ip]=$(( ${ips[$ip]:-0} + 1 ))
    paths[$path]=$(( ${paths[$path]:-0} + 1 ))
  else
    ((++skipped))
  fi
done < "$file"
printf 'requests: %d\nunique_ips: %d\n' "$requests" "${#ips[@]}"
printf 'status_2xx: %d\nstatus_3xx: %d\nstatus_4xx: %d\nstatus_5xx: %d\nskipped: %d\n' "$c2" "$c3" "$c4" "$c5" "$skipped"
echo "top_ips:"
for k in "${!ips[@]}"; do printf '%d %s\n' "${ips[$k]}" "$k"; done | LC_ALL=C sort -k1,1nr -k2,2 | head -n "$top"
echo "top_paths:"
for k in "${!paths[@]}"; do printf '%d %s\n' "${paths[$k]}" "$k"; done | LC_ALL=C sort -k1,1nr -k2,2 | head -n "$top"
