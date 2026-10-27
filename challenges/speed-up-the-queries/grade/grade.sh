#!/usr/bin/env bash
# Grader for speed-up-the-queries: start PostgreSQL in a container, run
# indexes.sql on the empty tables, load random data, then run each query with
# random parameters and read its plan. grade_db.py does the database work.
set -uo pipefail
# shellcheck source-path=SCRIPTDIR/../../..
# shellcheck source=scripts/grade-lib.sh
source "$GRADE_LIB"

POSTGRES_IMAGE=postgres:18.6-alpine3.23@sha256:1a0c23d53c1ad77468c8653fa6eed4f4105cfbef9e6f027bc1dfeb16949a8c29
# The postgres user of the Alpine image.
POSTGRES_UID=70
NAME=speed-up-the-queries-db
NETWORK=speed-up-the-queries-net
all_checks=(indexes orders-for-customer find-customer revenue-for-day)

timeout 300 docker pull --quiet "$POSTGRES_IMAGE" > /dev/null || exit 1
if [[ ! -x $GRADE_TOOLS/venv/bin/python ]]; then
  python3 -m venv "$GRADE_TOOLS/venv" || exit 1
  "$GRADE_TOOLS/venv/bin/pip" install --quiet --disable-pip-version-check --no-deps --only-binary :all: \
    --require-hashes -r "$GRADE_DIR/requirements-grader.txt" || exit 1
fi

# The server runs on an internal network: the runner can reach it, but it
# cannot reach anything else. The submission's SQL never runs as the
# superuser, and it has no way to start a program (see grade_db.py).
password=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
docker network create --internal "$NETWORK" > /dev/null || exit 1
docker run -d --name "$NAME" \
  --network "$NETWORK" \
  --read-only \
  --tmpfs /var/lib/postgresql:rw,size=1g,mode=1777 \
  --tmpfs /var/run/postgresql:rw,mode=1777 \
  --tmpfs /tmp:rw,mode=1777 \
  --user "$POSTGRES_UID:$POSTGRES_UID" \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --memory 2g \
  --pids-limit 256 \
  --cpus 2 \
  --shm-size 256m \
  -e POSTGRES_PASSWORD="$password" \
  -e PGDATA=/var/lib/postgresql/data \
  "$POSTGRES_IMAGE" \
  postgres \
  -c fsync=off -c synchronous_commit=off -c full_page_writes=off \
  -c max_parallel_workers_per_gather=0 -c jit=off -c shared_buffers=256MB \
  -c max_prepared_transactions=0 -c log_min_messages=warning > /dev/null || exit 1
ip=$(docker inspect --format "{{(index .NetworkSettings.Networks \"$NETWORK\").IPAddress}}" "$NAME")
[[ $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || exit 1

results="$GRADE_OUT/db-results.json"
PGHOST=$ip PGPORT=5432 PGPASSWORD=$password timeout 900 \
  "$GRADE_TOOLS/venv/bin/python" -I "$GRADE_DIR/grade_db.py" run "$GRADE_WORK" > "$results" 2> "$GRADE_OUT/db-grader.err"
status=$?
echo "::group::grader errors"
print_untrusted "$GRADE_OUT/db-grader.err"
echo "::endgroup::"
echo "::group::server log"
docker logs "$NAME" > "$GRADE_OUT/db-server.log" 2>&1
tail -n 80 "$GRADE_OUT/db-server.log" > "$GRADE_OUT/db-server-tail.log"
print_untrusted "$GRADE_OUT/db-server-tail.log"
echo "::endgroup::"
docker rm -f "$NAME" > /dev/null 2>&1 || true
docker network rm "$NETWORK" > /dev/null 2>&1 || true
if ((status != 0)); then
  exit 1
fi

for check in "${all_checks[@]}"; do
  IFS=$'\t' read -r check_status detail < <(python3 -I -c '
import json, sys
r = json.load(open(sys.argv[1]))[sys.argv[2]]
print(r["status"], r["detail"], sep="\t")
' "$results" "$check")
  record "$check" "$check_status" "$detail"
done
