#!/usr/bin/env bash
# Grader for fix-the-deployment: create a kind cluster, apply the submitted
# manifests, then check the rollout, the Service and the pods.
set -uo pipefail
# shellcheck source-path=SCRIPTDIR/../../..
# shellcheck source=scripts/grade-lib.sh
source "$GRADE_LIB"

KIND_VERSION=v0.33.0
KIND_SHA256=aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d
KUBECTL_VERSION=v1.37.1
KUBECTL_SHA256=65691ff77eb6fa44c908b77a1082c9f092c3b9733b5cefabec0d1104890e21a8
NODE_IMAGE=kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
APP_IMAGE=devops-daily/inventory-api:1.0
CLUSTER=challenge
NODE=$CLUSTER-control-plane
NS=challenge
ROLLOUT_SECONDS=120
STORE=riverside

work=$GRADE_WORK
out=$GRADE_OUT
export KUBECONFIG="$out/kubeconfig"

skip() {
  local reason=$1
  shift
  for check in "$@"; do record "$check" skip "$reason"; done
}

# --- tools and cluster (all from main, nothing from the PR) ---------------------
fetch "https://github.com/kubernetes-sigs/kind/releases/download/$KIND_VERSION/kind-linux-amd64" "$KIND_SHA256" "$GRADE_TOOLS/kind" || exit 1
fetch "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/amd64/kubectl" "$KUBECTL_SHA256" "$GRADE_TOOLS/kubectl" || exit 1
chmod +x "$GRADE_TOOLS/kind" "$GRADE_TOOLS/kubectl"
kind=$GRADE_TOOLS/kind
kubectl() { timeout 180 "$GRADE_TOOLS/kubectl" "$@"; }

timeout 600 docker build --quiet -t "$APP_IMAGE" "$work/app" || exit 1
timeout 600 "$kind" create cluster --name "$CLUSTER" --image "$NODE_IMAGE" --wait 180s || exit 1
timeout 300 "$kind" load docker-image "$APP_IMAGE" --name "$CLUSTER" || exit 1

# check_manifests.py refuses privileged pods, host namespaces, hostPath and
# root before anything is applied. Pod Security "restricted" on the namespace
# is the second layer.
kubectl create namespace "$NS" || exit 1
kubectl label namespace "$NS" \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/enforce-version=latest || exit 1

# --- manifests ------------------------------------------------------------------
objects="$out/objects.json"
checked="$out/checked.json"
shopt -s nullglob
files=("$work"/k8s/*.yaml "$work"/k8s/*.yml)
shopt -u nullglob
if ((${#files[@]} == 0)); then
  record manifests fail "There are no YAML files in k8s/."
  skip "Skipped: the manifests were not applied." rollout endpoints answers stable
  exit 0
fi
args=()
for f in "${files[@]}"; do args+=(-f "$f"); done
if ! kubectl create --dry-run=client -n "$NS" -o json "${args[@]}" > "$objects" 2> "$out/parse.err"; then
  record manifests fail "kubectl could not read the manifests: $(head -n 1 "$out/parse.err" | cut -c1-200)"
  skip "Skipped: the manifests were not applied." rollout endpoints answers stable
  exit 0
fi
if ! problems=$(python3 "$GRADE_DIR/check_manifests.py" "$objects" "$checked"); then
  record manifests fail "$(head -n 3 <<<"$problems" | paste -sd ' ' -)"
  skip "Skipped: the manifests were not applied." rollout endpoints answers stable
  exit 0
fi
# Apply exactly the objects that were checked, not the files again.
if ! kubectl apply -n "$NS" -f "$checked" > "$out/apply.log" 2>&1; then
  record manifests fail "kubectl apply failed: $(grep -m1 -iE 'error|invalid|forbidden' "$out/apply.log" | cut -c1-200)"
  print_untrusted "$out/apply.log"
  skip "Skipped: the manifests were not applied." rollout endpoints answers stable
  exit 0
fi
print_untrusted "$out/apply.log"
record manifests pass "Applied a Deployment, a Service and $(python3 -c 'import json,sys; print(sum(1 for o in json.load(open(sys.argv[1]))["items"] if o.get("kind")=="ConfigMap"))' "$checked") ConfigMap(s)."

# --- rollout --------------------------------------------------------------------
rolled_out=false
if kubectl -n "$NS" rollout status deployment/inventory-api --timeout="${ROLLOUT_SECONDS}s" > "$out/rollout.log" 2>&1; then
  rolled_out=true
  record rollout pass "2 of 2 replicas are ready."
else
  kubectl -n "$NS" get pods -o json > "$out/pods.json" 2>/dev/null
  record rollout fail "After $ROLLOUT_SECONDS seconds: $(python3 "$GRADE_DIR/pod_states.py" "$out/pods.json")"
fi
echo "::group::cluster state"
{
  kubectl -n "$NS" get all,endpointslices -o wide
  kubectl -n "$NS" describe pods
  kubectl -n "$NS" get events --sort-by=.lastTimestamp
} > "$out/state.log" 2>&1
print_untrusted "$out/state.log"
echo "::endgroup::"

# --- endpoints ------------------------------------------------------------------
# The EndpointSlice controller can lag the rollout by a moment; when the pods
# rolled out, give it up to 20 seconds.
for _ in $(seq 1 10); do
  read -r total ready < <(kubectl -n "$NS" get endpointslices -l kubernetes.io/service-name=inventory-api -o json |
    python3 -c '
import json, sys
eps = [e for s in json.load(sys.stdin).get("items", []) for e in (s.get("endpoints") or [])]
print(len(eps), sum(1 for e in eps if (e.get("conditions") or {}).get("ready")))
')
  [[ $rolled_out == true && ${ready:-0} -eq 0 ]] || break
  sleep 2
done
total=${total:-0}
ready=${ready:-0}
if ((ready > 0)); then
  record endpoints pass "The Service routes to $ready ready pod(s)."
elif ((total > 0)); then
  record endpoints fail "The Service selects $total pod(s), but none of them is ready."
else
  record endpoints fail "The Service does not select any pod: it has no endpoints."
fi

# --- answers --------------------------------------------------------------------
cluster_ip=$(kubectl -n "$NS" get service inventory-api -o jsonpath='{.spec.clusterIP}')
if [[ -z $cluster_ip || $cluster_ip == None ]]; then
  record answers fail "The Service has no cluster IP."
else
  # kube-proxy takes a few seconds to replace the "no endpoints" reject rule
  # once pods turn ready, so keep trying for up to 30 seconds.
  for _ in $(seq 1 15); do
    response=$(docker exec "$NODE" curl -s --max-time 5 -w '\n%{http_code}' "http://$cluster_ip:80/" 2>/dev/null)
    curl_status=$?
    [[ $curl_status -eq 0 && ${response##*$'\n'} != 000 ]] && break
    sleep 2
  done
  code=${response##*$'\n'}
  body=${response%$'\n'*}
  if [[ $curl_status -ne 0 || $code == 000 ]]; then
    record answers fail "Nothing answered on port 80 of the Service (curl exit code $curl_status)."
  elif [[ $code != 200 ]]; then
    record answers fail "GET / on port 80 of the Service answered HTTP $code, not 200."
  elif [[ $(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("store",""))' "$body" 2>/dev/null) != "$STORE" ]]; then
    record answers fail "GET / answered 200, but the response does not show the store name from the ConfigMap ($STORE)."
  else
    record answers pass "GET / on port 80 of the Service answered 200 for store $STORE."
  fi
fi

# --- stable ---------------------------------------------------------------------
# Wait, then call the URLs the probes call (a liveness probe with a long delay
# could otherwise hide a wrong path), count restarts and check readiness again.
# A probe must answer 2xx itself: a redirect is not followed and counts as a
# failure, so a path the app only redirects cannot pass here.
if [[ $rolled_out == true ]]; then
  sleep 30
  kubectl -n "$NS" get pods -o json > "$out/pods-after.json"
  bad_probes=()
  while IFS= read -r -d '' probe && IFS= read -r -d '' ip && IFS= read -r -d '' port && IFS= read -r -d '' path; do
    if [[ ! $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ || ! $port =~ ^[0-9]{1,5}$ || ! $path =~ ^/[A-Za-z0-9._~/%-]{0,200}$ ]]; then
      bad_probes+=("$probe")
      continue
    fi
    code=$(docker exec "$NODE" curl --globoff -s -o /dev/null -w '%{http_code}' --max-time 3 "http://$ip:$port$path" 2>/dev/null || true)
    [[ $code =~ ^2[0-9][0-9]$ ]] || bad_probes+=("$probe")
  done < <(python3 "$GRADE_DIR/probe_targets.py" "$out/pods-after.json")
  restarts=$(python3 -c '
import json, sys
pods = json.load(open(sys.argv[1])).get("items", [])
print(sum(int(c.get("restartCount") or 0) for p in pods for c in (p.get("status") or {}).get("containerStatuses") or []))
' "$out/pods-after.json")
  ready_now=$(kubectl -n "$NS" get deployment inventory-api -o jsonpath='{.status.readyReplicas}')
  if ((${#bad_probes[@]} > 0)); then
    names=$(printf '%s\n' "${bad_probes[@]}" | sort -u | paste -sd, - | sed 's/,/, /g')
    record stable fail "A probe request does not get a 2xx answer when the grader sends it to a running pod: $names."
  elif [[ ${restarts:-1} != 0 ]]; then
    record stable fail "Containers restarted ${restarts} time(s)."
  elif [[ ${ready_now:-0} != 2 ]]; then
    record stable fail "30 seconds later, ${ready_now:-0} of 2 replicas are ready."
  else
    record stable pass "Both probes answer, 2 of 2 replicas are still ready and no container restarted."
  fi
else
  record stable skip "Skipped: the Deployment did not roll out."
fi

timeout 120 "$kind" delete cluster --name "$CLUSTER" > /dev/null 2>&1 || true
