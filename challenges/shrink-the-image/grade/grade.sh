#!/usr/bin/env bash
# Grader for shrink-the-image: build the image, then check its size, its user,
# that it starts, and that the app still works.
set -uo pipefail
# shellcheck source-path=SCRIPTDIR/../../..
# shellcheck source=scripts/grade-lib.sh
source "$GRADE_LIB"

LIMIT_MB=200
START_SECONDS=20
IMAGE=challenge/shrink-the-image:graded
NAME=shrink-the-image-app
PORT=18000
work=$GRADE_WORK

skip() {
  local reason=$1
  shift
  for check in "$@"; do record "$check" skip "$reason"; done
}

docker info --format 'Docker {{.ServerVersion}}, storage driver {{.Driver}}'

# --- build ----------------------------------------------------------------------
dockerfile="$work/Dockerfile"
if [[ ! -f $dockerfile ]]; then
  record build fail "There is no Dockerfile in the challenge folder."
  skip "Skipped: the image did not build." size non-root starts works
  exit 0
fi

# A "# syntax=" line makes BuildKit run a frontend image of the author's
# choice. Only Docker's own Dockerfile frontend is allowed.
syntax=$(awk '
  /^[[:space:]]*#/ {
    line = tolower($0)
    if (line ~ /^[[:space:]]*#[[:space:]]*syntax[[:space:]]*=/) { sub(/^[^=]*=[[:space:]]*/, "", line); print line; exit }
    next
  }
  { exit }
' "$dockerfile")
if [[ -n $syntax && ! $syntax =~ ^(docker\.io/)?docker/dockerfile(-upstream)?(:[a-z0-9.-]+)?(@sha256:[0-9a-f]{64})?[[:space:]]*$ ]]; then
  record build fail "The Dockerfile asks for a custom syntax frontend. Only docker/dockerfile is allowed."
  skip "Skipped: the image did not build." size non-root starts works
  exit 0
fi

build_log="$GRADE_OUT/build.log"
start=$(date +%s)
DOCKER_BUILDKIT=1 timeout 600 docker build --pull --progress=plain -t "$IMAGE" "$work" > "$build_log" 2>&1
build_status=$?
echo "::group::docker build log"
print_untrusted "$build_log"
echo "::endgroup::"
if [[ $build_status -ne 0 ]]; then
  if [[ $build_status -eq 124 ]]; then
    record build fail "docker build did not finish within 10 minutes."
  else
    last_error=$(grep -E '^(ERROR|error):' "$build_log" | tail -n 1 | cut -c1-200)
    record build fail "docker build failed. ${last_error:-See the build log in the workflow run.}"
  fi
  skip "Skipped: the image did not build." size non-root starts works
  exit 0
fi
record build pass "Built in $(($(date +%s) - start)) seconds."

# --- size -----------------------------------------------------------------------
bytes=$(docker image inspect --format '{{.Size}}' "$IMAGE")
mb=$(((bytes + 500000) / 1000000))
docker image ls "$IMAGE"
if ((bytes <= LIMIT_MB * 1000000)); then
  record size pass "The image is $mb MB. The limit is $LIMIT_MB MB."
else
  record size fail "The image is $mb MB. The limit is $LIMIT_MB MB."
fi

# --- starts ---------------------------------------------------------------------
config_user=$(docker image inspect --format '{{.Config.User}}' "$IMAGE")
cid=$(docker run -d --name "$NAME" \
  -p "127.0.0.1:$PORT:8000" \
  --memory 512m --pids-limit 256 --cpus 1 \
  --security-opt no-new-privileges \
  "$IMAGE" 2> "$GRADE_OUT/run.err")
run_status=$?
started=false
if [[ $run_status -ne 0 ]]; then
  record starts fail "docker run failed: $(head -c 200 "$GRADE_OUT/run.err")"
else
  code=000
  t0=$(date +%s%N)
  deadline=$(($(date +%s) + START_SECONDS))
  while (($(date +%s) < deadline)); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$PORT/health" || true)
    [[ $code == 200 ]] && break
    [[ $(docker inspect --format '{{.State.Running}}' "$cid") == true ]] || break
    sleep 0.5
  done
  if [[ $code == 200 ]]; then
    started=true
    ms=$((($(date +%s%N) - t0) / 1000000))
    record starts pass "GET /health answered 200 after $((ms / 1000)).$(((ms % 1000) / 100)) seconds."
  elif [[ $(docker inspect --format '{{.State.Running}}' "$cid") != true ]]; then
    record starts fail "The container exited with code $(docker inspect --format '{{.State.ExitCode}}' "$cid") before GET /health answered."
  else
    record starts fail "GET /health on port 8000 did not answer 200 within $START_SECONDS seconds (last status: $code)."
  fi
  echo "::group::container logs"
  docker logs "$cid" > "$GRADE_OUT/container.log" 2>&1
  tail -n 50 "$GRADE_OUT/container.log" > "$GRADE_OUT/container-tail.log"
  print_untrusted "$GRADE_OUT/container-tail.log"
  echo "::endgroup::"
fi

# --- non-root -------------------------------------------------------------------
case ${config_user%%:*} in
  "" )
    record non-root fail "The image has no USER instruction, so it runs as root." ;;
  root | 0 )
    record non-root fail "The image sets USER to root." ;;
  * )
    if [[ $started == true ]]; then
      uids=$(docker top "$cid" -eo uid | tail -n +2 | tr -d ' ' | sort -u | paste -sd, -)
      if [[ ,$uids, == *,0,* ]]; then
        record non-root fail "USER is set, but a process in the container runs as UID 0."
      else
        record non-root pass "Processes run as UID $uids."
      fi
    else
      record non-root fail "USER is set, but the container did not start, so its processes could not be checked."
    fi
    ;;
esac

# --- works ----------------------------------------------------------------------
if [[ $started == true ]]; then
  word=$(LC_ALL=C tr -dc '[:lower:]' < /dev/urandom | head -c 8)
  number=$((RANDOM % 9000 + 1000))
  text="Shrink $word Image $number"
  want="shrink-$word-image-$number"
  body=$(curl -s --max-time 5 -G --data-urlencode "text=$text" "http://127.0.0.1:$PORT/slugify" || true)
  got=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("slug",""))' "$body" 2>/dev/null || true)
  if [[ $got == "$want" ]]; then
    record works pass "GET /slugify returned the right slug for a random text."
  else
    record works fail "GET /slugify did not return the right slug for a random text."
  fi
else
  record works skip "Skipped: the app did not start."
fi

docker rm -f "$NAME" > /dev/null 2>&1 || true
