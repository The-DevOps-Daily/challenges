"""Print the URL each HTTP probe of the inventory-api pods calls.

Reads `kubectl get pods -o json` and prints "<probe> <url>" per running pod
and probe, so the grader can call the same URLs the kubelet calls.
"""

from __future__ import annotations

import json
import sys


def port_number(port, container: dict) -> int | None:
    if isinstance(port, int):
        return port
    for item in container.get("ports") or []:
        if item.get("name") == port:
            return item.get("containerPort")
    return None


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as fh:
        pods = json.load(fh).get("items") or []
    for pod in pods:
        ip = (pod.get("status") or {}).get("podIP")
        if not ip or (pod.get("status") or {}).get("phase") != "Running":
            continue
        for container in (pod.get("spec") or {}).get("containers") or []:
            for name in ("readinessProbe", "livenessProbe"):
                http = (container.get(name) or {}).get("httpGet") or {}
                port = port_number(http.get("port"), container)
                if port is None:
                    print(f"{name} invalid")
                    continue
                path = http.get("path") or "/"
                if not path.startswith("/"):
                    path = "/" + path
                print(f"{name} http://{ip}:{port}{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
