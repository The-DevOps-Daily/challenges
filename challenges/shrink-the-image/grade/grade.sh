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
# Every build uses Docker's official Dockerfile frontend, pinned by digest.
# BUILDKIT_SYNTAX takes precedence over any syntax directive in the file, so
# a submission cannot pick the frontend even if a directive slips past the
# check below.
FRONTEND=docker/dockerfile:1.28.0@sha256:bb22d9815c728170f72750f4e5b0d672e06176142e1d602c7e66c050100b7e5b
NAME=shrink-the-image-app
NETWORK=shrink-the-image-net
# A static busybox from Docker's official image reports which UID the
# image's USER really gets, without running anything from the submission.
BUSYBOX=busybox:1.37.0-musl@sha256:5cec3fc171c87218698e85a52af7087de727372aae264a787b8112901a5b0092
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

# A syntax directive would make BuildKit run a frontend image of the
# author's choice. The build pins the frontend anyway (FRONTEND above); this
# check gives a clear message first. BuildKit skips a UTF-8 byte order mark,
# so strip one before looking, and look at every line, in # and // forms.
preflight=$(python3 -I - "$dockerfile" <<'PY'
import re, sys
data = open(sys.argv[1], "rb").read()
if b"\0" in data:
    print("nul")
    sys.exit()
if data.startswith(b"\xef\xbb\xbf"):
    data = data[3:]
directive = re.compile(rb"^[ \t]*(#|//)[ \t]*syntax[ \t]*=", re.IGNORECASE)
if any(directive.match(line) for line in data.splitlines()):
    print("syntax")
PY
)
if [[ $preflight == nul ]]; then
  record build fail "The Dockerfile contains NUL bytes; it must be a text file."
  skip "Skipped: the image did not build." size non-root starts works
  exit 0
elif [[ $preflight == syntax ]]; then
  record build fail "The Dockerfile has a syntax directive (a comment line with syntax=). Remove it: the grader always builds with Docker's own Dockerfile frontend."
  skip "Skipped: the image did not build." size non-root starts works
  exit 0
elif [[ -n $preflight ]]; then
  exit 1
fi

build_log="$GRADE_OUT/build.log"
start=$(date +%s)
DOCKER_BUILDKIT=1 timeout 600 docker build --pull --progress=plain \
  --build-arg "BUILDKIT_SYNTAX=$FRONTEND" \
  -t "$IMAGE" "$work" > "$build_log" 2>&1
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
# The container runs on an internal network: the runner can reach it, but it
# cannot reach anything else, so it cannot hand requests to a server
# elsewhere.
config_user=$(docker image inspect --format '{{.Config.User}}' "$IMAGE")
docker network create --internal "$NETWORK" > /dev/null || exit 1
cid=$(docker run -d --name "$NAME" \
  --network "$NETWORK" \
  --memory 512m --pids-limit 256 --cpus 1 \
  --security-opt no-new-privileges \
  "$IMAGE" 2> "$GRADE_OUT/run.err")
run_status=$?
started=false
ip=""
if [[ $run_status -eq 0 ]]; then
  ip=$(docker inspect --format "{{(index .NetworkSettings.Networks \"$NETWORK\").IPAddress}}" "$cid" 2>/dev/null)
fi
if [[ $run_status -ne 0 ]]; then
  record starts fail "docker run failed: $(head -c 200 "$GRADE_OUT/run.err")"
elif [[ ! $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
  record starts fail "The container stopped at once (exit code $(docker inspect --format '{{.State.ExitCode}}' "$cid" 2>/dev/null))."
else
  base="http://$ip:8000"
  code=000
  t0=$(date +%s%N)
  deadline=$(($(date +%s) + START_SECONDS))
  while (($(date +%s) < deadline)); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$base/health" || true)
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
# USER can name a user whose UID is 0, and a root entrypoint can drop to
# another user before the grader looks at the processes. So first ask Docker
# itself: start the image with its own USER but with the trusted busybox as
# the entrypoint, and read the UID it runs as. Then check the processes of
# the real container too.
user=${config_user%%:*}
uid=""
if [[ -n $user ]]; then
  trusted="$GRADE_TOOLS/trusted"
  if [[ ! -x $trusted/busybox ]]; then
    mkdir -p "$trusted"
    timeout 120 docker pull --quiet "$BUSYBOX" > /dev/null || exit 1
    bb=$(docker create "$BUSYBOX") || exit 1
    docker cp "$bb:/bin/busybox" "$trusted/busybox" > /dev/null || exit 1
    docker rm -f "$bb" > /dev/null
  fi
  # A fresh random directory, so nothing in the image can sit at that path.
  # The file keeps the name busybox, which busybox needs to run its applets.
  probe="/.grader-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
  if probe_out=$(timeout 60 docker run --rm --network none --pull never --no-healthcheck \
    --memory 128m --pids-limit 16 \
    --entrypoint "$probe/busybox" \
    -v "$trusted:$probe:ro" \
    "$IMAGE" id -u 2> "$GRADE_OUT/probe.err"); then
    [[ $probe_out =~ ^[0-9]{1,10}$ ]] && uid=$probe_out
  fi
fi
if [[ -z $user ]]; then
  record non-root fail "The image has no USER instruction, so it runs as root."
elif [[ ! $uid =~ ^[0-9]+$ ]]; then
  record non-root fail "Docker could not start the image as USER $user: $(head -n 1 "$GRADE_OUT/probe.err" | cut -c1-160)"
elif ((uid == 0)); then
  record non-root fail "USER resolves to UID 0, which is root."
elif [[ $started != true ]]; then
  record non-root fail "USER is set, but the container did not start, so its processes could not be checked."
else
  uids=$(docker top "$cid" -eo pid,uid 2>/dev/null | awk 'NR > 1 && $2 ~ /^[0-9]+$/ { print $2 }' | sort -un | paste -sd, -)
  if [[ -z $uids ]]; then
    record non-root fail "Could not read the processes in the container."
  elif [[ ,$uids, == *,0,* ]]; then
    record non-root fail "USER is set, but a process in the container runs as UID 0."
  else
    record non-root pass "USER resolves to UID $uid, and processes run as UID $uids."
  fi
fi

# --- works ----------------------------------------------------------------------
# check_app.py sends random Latin, Greek and Cyrillic texts and compares the
# slugs with python-slugify itself, then checks Flask's error answers and the
# gunicorn Server header. It runs in a venv with pinned, hashed packages.
if [[ $started == true ]]; then
  if [[ ! -x $GRADE_TOOLS/venv/bin/python ]]; then
    python3 -m venv "$GRADE_TOOLS/venv" || exit 1
    "$GRADE_TOOLS/venv/bin/pip" install --quiet --disable-pip-version-check --no-deps --require-hashes \
      -r "$GRADE_DIR/requirements-grader.txt" || exit 1
  fi
  if works_detail=$("$GRADE_TOOLS/venv/bin/python" -I "$GRADE_DIR/check_app.py" "$base"); then
    record works pass "$works_detail"
  else
    record works fail "$works_detail"
  fi
else
  record works skip "Skipped: the app did not start."
fi

docker rm -f "$NAME" > /dev/null 2>&1 || true
docker network rm "$NETWORK" > /dev/null 2>&1 || true
