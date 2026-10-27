#!/usr/bin/bash
#
# summarize.sh: print a short report for a web server access log.
#
# Usage: ./summarize.sh [-n N] [LOGFILE]

top=5

while getopts "n:" opt; do
  case $opt in
    n) top=$OPTARG ;;
  esac
done
shift $((OPTIND - 1))

file=$1

# Ensure a log file was provided and exists
if [[ -z "$file" || ! -f "$file" ]]; then
  echo "Error: Please provide a valid log file path." >&2
  echo "Usage: $0 [-n N] [LOGFILE]" >&2
  exit 1
fi

# Basic metrics
total=$(grep -c "^." "$file")
ips=$(awk '{print $1}' "$file" | sort -u | wc -l)
skipped=$(grep -c "^$" "$file")

echo "requests: $total"
echo "unique_ips: $ips"

# Generate status classes dynamically (mapping 1xx, 2xx, 3xx, 4xx, 5xx to 0 if missing)
declare -A status_counts
for c in 1 2 3 4 5; do status_counts[$c]=0; done

while read -r count class; do
  status_counts[$class]=$count
done < <(awk '{print $9}' "$file" | grep -E '^[1-5][0-9][0-9]$' | cut -c1 | sort | uniq -c)

for c in 2 3 4 5; do
  echo "status_${c}xx: ${status_counts[$c]}"
done

echo "skipped: $skipped"

echo "top_ips:"
awk '{print $1}' "$file" | sort | uniq -c | sort -rn | head -n "$top" | awk '{print $1 " " $2}'

echo "top_paths:"
awk '{print $7}' "$file" | sort | uniq -c | sort -rn | head -n "$top" | awk '{print $1 " " $2}'
