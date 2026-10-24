"""Check the objects in the submitted manifests before anything is applied.

  check_manifests.py OBJECTS_JSON CHECKED_LIST_JSON

Reads what `kubectl create --dry-run=client -o json` printed for the k8s/
folder (one JSON document per object, or a List) and prints one line per
problem. With no problems it writes the objects as one List to
CHECKED_LIST_JSON, which is what the grader applies, and exits 0.
The rules keep the submission to the objects the challenge is about, and keep
pods away from the node (the grader's cluster runs on the CI runner).
"""

from __future__ import annotations

import json
import sys

IMAGE = "devops-daily/inventory-api:1.0"
MAX_PROBE_SECONDS = 60
NAMESPACE = "challenge"
ALLOWED = {("apps/v1", "Deployment"), ("v1", "Service"), ("v1", "ConfigMap")}
SAFE_VOLUMES = {"configMap", "emptyDir", "downwardAPI", "projected"}
SAFE_PROJECTIONS = {"configMap", "downwardAPI"}


def read_documents(text: str) -> list[dict]:
    decoder = json.JSONDecoder()
    docs, pos = [], 0
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            return docs
        doc, pos = decoder.raw_decode(text, pos)
        docs.append(doc)


def objects(docs: list) -> list:
    items: list = []
    for doc in docs:
        if isinstance(doc, dict) and doc.get("kind") == "List":
            items.extend(doc.get("items") or [])
        else:
            items.append(doc)
    return items


def check_probe(name: str, probe: dict, problems: list[str]) -> None:
    http = probe.get("httpGet")
    if not http or set(probe) & {"exec", "tcpSocket", "grpc"}:
        problems.append(f"The {name} must be an HTTP (httpGet) probe.")
        return
    if http.get("scheme", "HTTP") != "HTTP" or http.get("host"):
        problems.append(f"The {name} must call the pod over plain HTTP, without a host.")
    for field in ("initialDelaySeconds", "periodSeconds", "timeoutSeconds"):
        value = probe.get(field, 0)
        if not isinstance(value, int) or value > MAX_PROBE_SECONDS:
            problems.append(f"The {name} sets {field} above {MAX_PROBE_SECONDS} seconds.")
    threshold = probe.get("failureThreshold", 3)
    if not isinstance(threshold, int) or threshold > 10:
        problems.append(f"The {name} sets failureThreshold above 10.")


def store_from_configmap(container: dict) -> bool:
    """STORE_NAME is set from a ConfigMap, not written into the Deployment.

    Whether the referenced key exists is left to the cluster: a wrong key is
    one of the bugs to find at run time.
    """
    for env in container.get("env") or []:
        if env.get("name") == "STORE_NAME":
            ref = (env.get("valueFrom") or {}).get("configMapKeyRef") or {}
            return bool(ref.get("name") and ref.get("key"))
    return any((source.get("configMapRef") or {}).get("name") for source in container.get("envFrom") or [])


def check_pod_spec(spec: dict, problems: list[str]) -> None:
    for key in ("hostNetwork", "hostPID", "hostIPC"):
        if spec.get(key):
            problems.append(f"The pod template sets {key}. Pods must not share the node's namespaces.")
    if spec.get("initContainers") or spec.get("ephemeralContainers"):
        problems.append("The pod template adds init or ephemeral containers. Keep the single api container.")
    for volume in spec.get("volumes") or []:
        kinds = set(volume) - {"name"}
        if not kinds <= SAFE_VOLUMES:
            problems.append("The pod template mounts a volume type other than configMap, emptyDir, downwardAPI or projected.")
        for source in (volume.get("projected") or {}).get("sources") or []:
            if not set(source) <= SAFE_PROJECTIONS:
                problems.append("A projected volume uses a source other than configMap or downwardAPI.")
    pod_sc = spec.get("securityContext") or {}
    if pod_sc.get("sysctls"):
        problems.append("The pod template sets sysctls.")

    containers = spec.get("containers") or []
    if len(containers) != 1:
        problems.append(f"The pod template has {len(containers)} containers. Keep the single api container.")
    for container in containers:
        if container.get("image") != IMAGE:
            problems.append(f"The container image must stay {IMAGE}.")
        sc = container.get("securityContext") or {}
        if sc.get("privileged"):
            problems.append("The container is privileged.")
        if (sc.get("capabilities") or {}).get("add"):
            problems.append("The container adds Linux capabilities.")
        if sc.get("procMount") not in (None, "Default"):
            problems.append("The container changes procMount.")
        for port in container.get("ports") or []:
            if port.get("hostPort"):
                problems.append("The container uses a hostPort.")
        for probe in ("readinessProbe", "livenessProbe"):
            if not container.get(probe):
                problems.append(f"The container has no {probe}. Keep both probes and make them work.")
            else:
                check_probe(probe, container[probe], problems)
        if container.get("startupProbe"):
            problems.append("The container adds a startupProbe. Keep to the readiness and liveness probes.")
        if not store_from_configmap(container):
            problems.append("STORE_NAME must come from a ConfigMap (configMapKeyRef or envFrom), not a value in the Deployment.")


def check(items: list) -> list[str]:
    problems: list[str] = []
    if len(items) > 10:
        return ["The manifests define more than 10 objects."]
    if not all(isinstance(obj, dict) for obj in items):
        return ["The manifests contain something that is not a Kubernetes object."]

    deployments, services = [], []
    for obj in items:
        kind = obj.get("kind")
        api = obj.get("apiVersion")
        meta = obj.get("metadata") or {}
        name = meta.get("name", "")
        if (api, kind) not in ALLOWED:
            problems.append(f"Only Deployment, Service and ConfigMap objects are allowed, found {kind} {name}.")
            continue
        if meta.get("namespace") not in (None, "", NAMESPACE):
            problems.append(f"{kind} {name} sets a namespace. Leave the namespace out; the grader picks it.")
        if kind == "Deployment":
            deployments.append(obj)
        elif kind == "Service":
            services.append(obj)

    names = [d["metadata"].get("name") for d in deployments]
    if names != ["inventory-api"]:
        problems.append("The manifests must define exactly one Deployment, named inventory-api.")
    names = [s["metadata"].get("name") for s in services]
    if names != ["inventory-api"]:
        problems.append("The manifests must define exactly one Service, named inventory-api.")

    for deployment in deployments:
        spec = deployment.get("spec") or {}
        if spec.get("replicas", 1) != 2:
            problems.append("The Deployment must run 2 replicas.")
        check_pod_spec(((spec.get("template") or {}).get("spec")) or {}, problems)

    for service in services:
        spec = service.get("spec") or {}
        if spec.get("type", "ClusterIP") != "ClusterIP":
            problems.append("The Service must be of type ClusterIP.")
        if spec.get("externalIPs") or spec.get("externalName"):
            problems.append("The Service must not set externalIPs or externalName.")
        if not spec.get("selector"):
            problems.append("The Service has no selector.")

    seen: list[str] = []
    for problem in problems:
        if problem not in seen:
            seen.append(problem)
    return seen


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as fh:
        text = fh.read()
    try:
        items = objects(read_documents(text))
    except ValueError:
        print("kubectl printed something that is not JSON for the manifests.")
        return 1
    problems = check(items)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump({"apiVersion": "v1", "kind": "List", "items": items}, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
