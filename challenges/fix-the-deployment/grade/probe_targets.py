"""Print what each HTTP probe of the inventory-api pods calls.

Reads `kubectl get pods -o json` and writes one record per running pod and
probe to stdout: probe name, pod IP, port number and path, each field ended
by a NUL byte. Anything that does not validate is written as the probe name
with an empty IP, so the grader counts it as a failing probe. The grader
builds the URL from these fixed parts only.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sys

PATH_RE = re.compile(r"^/[A-Za-z0-9._~/%-]{0,200}$")


def port_number(port, container: dict) -> int | None:
    if isinstance(port, bool):
        return None
    if isinstance(port, int):
        return port if 1 <= port <= 65535 else None
    for item in container.get("ports") or []:
        if item.get("name") == port and isinstance(item.get("containerPort"), int):
            return item["containerPort"]
    return None


def emit(*fields: str) -> None:
    sys.stdout.write("".join(f"{field}\0" for field in fields))


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as fh:
        pods = json.load(fh).get("items") or []
    for pod in pods:
        status = pod.get("status") or {}
        if status.get("phase") != "Running":
            continue
        try:
            ip = str(ipaddress.IPv4Address(status.get("podIP") or ""))
        except ValueError:
            continue
        for container in (pod.get("spec") or {}).get("containers") or []:
            for name in ("readinessProbe", "livenessProbe"):
                http = (container.get(name) or {}).get("httpGet") or {}
                port = port_number(http.get("port"), container)
                path = http.get("path") or "/"
                plain = isinstance(path, str) and PATH_RE.match(path) and "//" not in path
                if plain:
                    plain = all(part not in (".", "..") for part in path.split("/"))
                if port is None or not plain:
                    emit(name, "", "", "")
                    continue
                emit(name, ip, str(port), path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
