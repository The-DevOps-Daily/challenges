#!/usr/bin/env bash
#
# summarize.sh: print a short report for a web server access log.
#
# Usage: ./summarize.sh [-n N] [LOGFILE]

top=5

while getopts "n:" opt; do
  case $opt in
    n) top="$OPTARG" ;;
    *) echo "Usage: $0 [-n N] [LOGFILE]" >&2; exit 1 ;;
  esac
done
shift "$((OPTIND - 1))"

file="$1"

# Ensure a log file was provided and exists
if [[ -z "$file" || ! -f "$file" ]]; then
  echo "Error: Please provide a valid log file path." >&2
  echo "Usage: $0 [-n N] [LOGFILE]" >&2
  exit 1
fi

# Run everything through a unified, fast awk processor that handles line validation
awk -v top_limit="$top" '
BEGIN {
    # Initialize our dynamic status class tracking arrays
    status_classes[2] = 0
    status_classes[3] = 0
    status_classes[4] = 0
    status_classes[5] = 0
    skipped_count = 0
    valid_count = 0
}

# Hint 1: Match against standard Combined/Common Log Format schema to filter malformed lines.
# Expects: IP ... "METHOD PATH PROTOCOL" STATUS BYTES
/^[0-9a-fA-F:.]+ .*"[A-Z]+ [^" ]+ [^" ]+" [1-5][0-9][0-9] ([0-9]+|-)/ {
    valid_count++
    
    # Track the IP address (Field 1)
    ip_counts[$1]++
    
    # Track the requested HTTP path (Field 7)
    path_counts[$7]++
    
    # Isolate the leading digit of the HTTP Status Code (Field 9)
    status_prefix = substr($9, 1, 1)
    if (status_prefix in status_classes) {
        status_classes[status_prefix]++
    }
    next
}

# Any line failing the strict valid log line regex pattern above drops down here
{
    skipped_count++
}

END {
    # Output Basic Request and IP stats
    print "requests:", valid_count
    
    unique_ip_count = 0
    for (ip in ip_counts) { unique_ip_count++ }
    print "unique_ips:", unique_ip_count
    
    # Output Status Class Blocks
    for (i = 2; i <= 5; i++) {
        print "status_" i "xx:", status_classes[i]
    }
    
    print "skipped:", skipped_count

    # Process and sort top IP addresses
    print "top_ips:"
    cmd_ips = "sort -rn"
    for (ip in ip_counts) {
        print ip_counts[ip], ip | cmd_ips
    }
    close(cmd_ips)

    # Process and sort top Paths
    print "top_paths:"
    cmd_paths = "sort -rn"
    for (path in path_counts) {
        print path_counts[path], path | cmd_paths
    }
    close(cmd_paths)
}
' "$file" | awk -v top_limit="$top" '
# A lightweight secondary layout filter to cleanly truncate "top_" groups exactly at your $top variable threshold
# without triggering a premature SIGPIPE termination (Hint 4)
BEGIN { in_group = 0; count = 0 }
/^top_/ {
    print $0
    in_group = 1
    count = 0
    next
}
/^[a-z]/ {
    print $0
    in_group = 0
    next
}
in_group {
    if (count < top_limit) {
        print $0
        count++
    }
    next
}
'
