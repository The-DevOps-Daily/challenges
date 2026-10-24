"""Summarize why the inventory-api pods are not ready, without the fix.

Reads `kubectl get pods -o json` and prints one line such as
"0 of 2 pods are ready: 2 waiting (CreateContainerConfigError)."
"""

from __future__ import annotations

import json
import sys

HINTS = {
    "CrashLoopBackOff": "the container keeps exiting",
    "CreateContainerConfigError": "the container configuration is invalid",
    "ErrImagePull": "the image cannot be pulled",
    "ImagePullBackOff": "the image cannot be pulled",
}


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as fh:
        pods = json.load(fh).get("items") or []
    if not pods:
        print("No inventory-api pods exist.")
        return 0

    ready = 0
    states: dict[str, int] = {}
    restarts = 0
    for pod in pods:
        status = pod.get("status") or {}
        conditions = {c.get("type"): c.get("status") for c in status.get("conditions") or []}
        if conditions.get("Ready") == "True":
            ready += 1
            continue
        container = (status.get("containerStatuses") or [{}])[0]
        restarts += int(container.get("restartCount") or 0)
        state = container.get("state") or {}
        if "waiting" in state:
            reason = state["waiting"].get("reason") or "Waiting"
            hint = HINTS.get(reason)
            label = f"waiting ({reason}, {hint})" if hint else f"waiting ({reason})"
        elif "running" in state:
            label = "running but not ready (the readiness probe does not pass)"
        elif "terminated" in state:
            label = f"terminated ({state['terminated'].get('reason') or 'Error'})"
        elif status.get("phase") == "Pending":
            reasons = [c.get("reason") for c in status.get("conditions") or [] if c.get("reason")]
            label = f"pending ({reasons[0]})" if reasons else "pending"
        else:
            label = status.get("phase") or "unknown"
        states[label] = states.get(label, 0) + 1

    parts = [f"{count} {label}" for label, count in sorted(states.items())]
    line = f"{ready} of {len(pods)} pods are ready"
    if parts:
        line += ": " + "; ".join(parts)
    if restarts:
        line += f". Containers restarted {restarts} times"
    print(line + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
