#!/usr/bin/env bash
# Run the summarize.sh under test with the given arguments, from inside the
# test data folder. In CI it runs in a throwaway container with no network,
# read-only files and an unprivileged user, so it cannot change the tests or
# the grader. SANDBOX=none runs it directly, for local use.
set -uo pipefail

: "${SOLUTION_DIR:?}" "${DATA_DIR:?}"

if [[ ${SANDBOX:-docker} == none ]]; then
  cd "$DATA_DIR" || exit 99
  exec timeout 30 bash "$SOLUTION_DIR/summarize.sh" "$@"
fi

: "${SANDBOX_IMAGE:?}"
exec timeout 45 docker run --rm -i \
  --network none \
  --read-only \
  --tmpfs /tmp:rw,size=64m,mode=1777 \
  --user 1000:1000 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --memory 512m \
  --pids-limit 256 \
  --cpus 2 \
  -v "$SOLUTION_DIR:/solution:ro" \
  -v "$DATA_DIR:/data:ro" \
  -w /data \
  "$SANDBOX_IMAGE" \
  timeout 30 bash /solution/summarize.sh "$@"
