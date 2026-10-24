#!/usr/bin/env bash
#
# summarize.sh: print a short report for a web server access log.
#
# Usage: ./summarize.sh [-n N] [LOGFILE]
#
# This is where the last person left it. It counts lines and not much else.
# README.md has the full spec for the report.

top=5

while getopts "n:" opt; do
  case $opt in
    n) top=$OPTARG ;;
  esac
done
shift $((OPTIND - 1))

file=$1

total=$(cat $file | wc -l)
ips=$(cat $file | awk '{print $1}' | sort -u | wc -l)

echo "requests: $total"
echo "unique_ips: $ips"
# TODO: status classes, skipped lines, top_ips and top_paths
# tweak
