"""Check the objects in the submitted manifests before anything is applied.

Reads the JSON that `kubectl create --dry-run=client -o json` printed for the
k8s/ folder and prints one line per problem. Exit code 0 means no problems.
The rules keep the submission to the objects the challenge is about, and keep
pods away from the node (the grader's cluster runs on the CI runner).
"""

from __future__ import annotations

import json
import sys

IMAGE = "devops-daily/inventory-api:1.0"
NAMESPACE = "challenge"
ALLOWED = {("apps/v1", "Deployment"), ("v1", "Service"), ("v1", "ConfigMap")}
SAFE_VOLUMES = {"configMap", "emptyDir", "downwardAPI", "projected"}
SAFE_PROJECTIONS = {"configMap", "downwardAPI"}


def objects(doc: dict) -> list[dict]:
    if doc.get("kind") == "List":
        return list(doc.get("items") or [])
    return [doc]


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


def check(doc: dict) -> list[str]:
    problems: list[str] = []
    items = objects(doc)
    if len(items) > 10:
        return ["The manifests define more than 10 objects."]

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
        doc = json.load(fh)
    problems = check(doc)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
