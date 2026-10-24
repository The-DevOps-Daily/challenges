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

# A syntax directive would make BuildKit run a frontend image of the
# author's choice. The build pins the frontend anyway (FRONTEND above); this
# check gives a clear message first. BuildKit skips a UTF-8 byte order mark,
# so strip one before looking, and look at every line, in # and // forms.
if [[ $(tr -d '\000' < "$dockerfile" | wc -c) -ne $(wc -c < "$dockerfile") ]]; then
  record build fail "The Dockerfile contains NUL bytes; it must be a text file."
  skip "Skipped: the image did not build." size non-root starts works
  exit 0
fi
if sed '1s/^\xEF\xBB\xBF//' "$dockerfile" | grep -Eiq '^[[:space:]]*(#|//)[[:space:]]*syntax[[:space:]]*='; then
  record build fail "The Dockerfile has a syntax directive (a comment line with syntax=). Remove it: the grader always builds with Docker's own Dockerfile frontend."
  skip "Skipped: the image did not build." size non-root starts works
  exit 0
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
      uids=$(docker top "$cid" -eo pid,uid 2>/dev/null | awk 'NR > 1 && $2 ~ /^[0-9]+$/ { print $2 }' | sort -un | paste -sd, -)
      if [[ -z $uids ]]; then
        record non-root fail "Could not read the processes in the container."
      elif [[ ,$uids, == *,0,* ]]; then
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
# Random accented and non-Latin words: the right slugs need python-slugify's
# transliteration, so a hand-written stand-in for the app does not pass.
words=(
  "Crème|creme" "Brûlée|brulee" "Straße|strasse" "Ærøskøbing|aeroskobing" "Łódź|lodz"
  "naïve|naive" "café|cafe" "Dvořák|dvorak" "São|sao" "Zürich|zurich" "Kraków|krakow"
  "Málaga|malaga" "Øresund|oresund" "Smörgåsbord|smorgasbord" "piñata|pinata"
  "jalapeño|jalapeno" "façade|facade" "Göteborg|goteborg" "Reykjavík|reykjavik"
  "Ελλάδα|ellada" "Москва|moskva" "Déjà|deja" "Œuvre|oeuvre" "Fjörður|fjordur"
  "Ångström|angstrom" "Señor|senor" "Über|uber"
)
separators=(" " ", " " - " "! " "   " " / ")
if [[ $started == true ]]; then
  problems=()
  for round in 1 2 3; do
    text="" want=""
    for _ in 1 2 3 4; do
      pick=${words[RANDOM % ${#words[@]}]}
      text+="${pick%%|*}${separators[RANDOM % ${#separators[@]}]}"
      want+="${pick##*|}-"
    done
    number=$((RANDOM % 9000 + 1000))
    text+="$number"
    want+="$number"
    body=$(curl -s --max-time 5 -G --data-urlencode "text=$text" "http://127.0.0.1:$PORT/slugify" || true)
    got=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("slug",""))' "$body" 2>/dev/null || true)
    [[ $got == "$want" ]] || problems+=("round $round")
  done
  missing=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/slugify" || true)
  server=$(curl -s -o /dev/null -D - --max-time 5 "http://127.0.0.1:$PORT/health" | tr -d '\r' | awk -F': ' 'tolower($1) == "server" { print tolower($2) }')
  if ((${#problems[@]} > 0)); then
    record works fail "GET /slugify returned the wrong slug for ${#problems[@]} of 3 random texts."
  elif [[ $missing != 400 ]]; then
    record works fail "GET /slugify without text answered $missing, not 400."
  elif [[ $server != gunicorn* ]]; then
    record works fail "The app is not served by gunicorn, as in the starter's CMD."
  else
    record works pass "GET /slugify returned the right slugs for 3 random texts, served by gunicorn."
  fi
else
  record works skip "Skipped: the app did not start."
fi

docker rm -f "$NAME" > /dev/null 2>&1 || true
