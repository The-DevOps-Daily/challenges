#!/usr/bin/bash
#
# summarize.sh: print a short report for a web server access log.
#
# Usage: ./summarize.sh [-n N] [LOGFILE]

top=5

# Ensure we validate flags correctly according to spec rules
while getopts "n:" opt; do
  case $opt in
    n) 
      top="$OPTARG" 
      # Argument Validation: Ensure it is a valid integer and strictly greater than 0
      if [[ ! "$top" =~ ^[0-9]+$ ]]; then
        echo "Error: -n argument must be a valid integer." >&2
        exit 2
      fi
      if [ "$top" -eq 0 ]; then
        echo "Error: -n argument cannot be 0." >&2
        exit 2
      fi
      ;;
    *) 
      echo "Usage: $0 [-n N] [LOGFILE]" >&2
      exit 2 
      ;;
  esac
done
shift "$((OPTIND - 1))"

# Check if more than one log file is specified
if [ "$#" -gt 1 ]; then
  echo "Error: Too many arguments. Only one log file is allowed." >&2
  exit 2
fi

file="$1"

# Identify if we need to stream input data via stdin or a file path
if [[ -z "$file" || "$file" == "-" ]]; then
  # Direct handling for standard input streams
  input_source="cat"
else
  # Argument Validation: Ensure target log exists and is readable
  if [[ ! -e "$file" || ! -r "$file" ]]; then
    echo "Error: File '$file' does not exist or is unreadable." >&2
    exit 1
  fi
  input_source="cat \"$file\""
fi

# Execute data manipulation inside a single standalone high-speed AWK processor stage
eval "$input_source" | awk -v top_limit="$top" '
BEGIN {
    # Initialize zero tracking configurations for the status classes
    status_classes[2] = 0
    status_classes[3] = 0
    status_classes[4] = 0
    status_classes[5] = 0
    valid_count = 0
    skipped_count = 0
}

{
    # Handle files lacking trailing newlines; clean out tracking whitespace characters
    gsub(/\r$/, "", $0)
    
    # Ignore completely blank lines cleanly as skipped entries
    if ($0 == "") {
        skipped_count++
        next
    }

    # Match common/combined line token parameters cleanly 
    # Example format check: Client_IP Identity User [Date] "Method Path Protocol" Status Bytes
    if ($0 ~ /^[0-9a-fA-F:.]+ .*"[A-Z]+ [^" ]+ [^" ]+" [1-5][0-9][0-9] ([0-9]+|-)/) {
        valid_count++
        
        # Track active visitor client IPs
        ip_counts[$1]++
        
        # Isolate the requested HTTP Path string sequence
        path = $7
        # Edge-Case Trim: Strip active parameters or query paths down past the "?" character index boundary
        sub(/\?.*/, "", path)
        path_counts[path]++
        
        # Isolate and accumulate status codes
        status_prefix = substr($9, 1, 1)
        if (status_prefix in status_classes) {
            status_classes[status_prefix]++
        }
    } else {
        # Mark non-conforming malformed context lines as skipped
        skipped_count++
    }
}

END {
    # Render global accumulated output items to standard output
    print "requests:", valid_count
    
    unique_ip_count = 0
    for (ip in ip_counts) { unique_ip_count++ }
    print "unique_ips:", unique_ip_count
    
    for (i = 2; i <= 5; i++) {
        print "status_" i "xx:", status_classes[i]
    }
    
    print "skipped:", skipped_count

    # Aggregate, rank, and print top visitor client IP addresses up to the specified limit parameters
    print "top_ips:"
    cmd_ips = "sort -rn"
    for (ip in ip_counts) {
        print ip_counts[ip], ip | cmd_ips
    }
    close(cmd_ips)
    
    # Process, sort, and cleanly output top requested destination path strings
    print "top_paths:"
    cmd_paths = "sort -rn"
    for (path in path_counts) {
        print path_counts[path], path | cmd_paths
    }
    close(cmd_paths)
}
' | awk -v top_limit="$top" '
# A secondary light layout step to handle output pagination cleanly 
# This avoids killing the upstream stream with a quick SIGPIPE tool interruption call
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
