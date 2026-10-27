"""Check the shape of alerts.yml before any test runs.

  check_rules.py ALERTS_YML

Reads the file as data with PyYAML's safe loader, after refusing anchors,
aliases and tags (an alias bomb could blow up the memory of the runner).
Prints one line per problem and exits:

  0  the file has the four alerts with the right labels and annotations
  1  problems the PR comment should list; the scenario tests still run
  2  the file names a series or label that Prometheus reserves for alerts,
     or cannot be read; the scenario tests do not run

promtool check rules runs separately, in a container, for the PromQL itself.
"""

from __future__ import annotations

import sys

REQUIRED = {
    "CheckoutInstanceDown": "page",
    "CheckoutTargetsMissing": "page",
    "CheckoutHighErrorRate": "page",
    "CheckoutSlowRequests": "ticket",
}
# The tests read ALERTS, so no rule may write it or set the labels it uses.
RESERVED_SERIES = {"ALERTS", "ALERTS_FOR_STATE"}
RESERVED_LABELS = {"__name__", "alertname", "alertstate"}
GROUP_KEYS = {"name", "rules", "interval", "limit", "query_offset", "labels"}
RULE_KEYS = {"alert", "record", "expr", "for", "keep_firing_for", "labels", "annotations"}


class Unsafe(Exception):
    pass


def load(text: str):
    import yaml  # here, so the unit tests can use check() without PyYAML

    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise Unsafe("alerts.yml uses YAML anchors or aliases. Write each rule out in full.")
        if getattr(event, "tag", None) is not None:
            raise Unsafe("alerts.yml uses YAML tags. Keep it to plain mappings, lists and strings.")
    return yaml.safe_load(text)


def check(doc) -> tuple[list[str], bool]:
    """Return (problems, unsafe)."""
    problems: list[str] = []
    if not isinstance(doc, dict) or not isinstance(doc.get("groups"), list):
        return ["alerts.yml must be a mapping with a list of groups."], True
    if set(doc) != {"groups"}:
        problems.append("alerts.yml has top-level keys other than groups.")

    alerts: dict[str, list[dict]] = {}
    for group in doc["groups"]:
        if not isinstance(group, dict) or not isinstance(group.get("rules"), list):
            return ["Every group needs a name and a list of rules."], True
        if set(group) - GROUP_KEYS:
            problems.append(f"Group {group.get('name')!s:.40} has unknown keys.")
        if isinstance(group.get("labels"), dict) and set(group["labels"]) & RESERVED_LABELS:
            return [f"Group {group.get('name')!s:.40} sets a label that Prometheus reserves for alerts."], True
        for rule in group["rules"]:
            if not isinstance(rule, dict):
                return ["Every rule must be a mapping."], True
            if set(rule) - RULE_KEYS:
                problems.append("A rule has unknown keys.")
            labels = rule.get("labels") or {}
            if not isinstance(labels, dict):
                return ["Rule labels must be a mapping."], True
            if set(labels) & RESERVED_LABELS:
                return ["A rule sets __name__, alertname or alertstate as a label. Prometheus sets those itself."], True
            if "record" in rule:
                if str(rule["record"]) in RESERVED_SERIES:
                    return ["A recording rule writes ALERTS or ALERTS_FOR_STATE, which only Prometheus may write."], True
                continue
            name = rule.get("alert")
            if isinstance(name, str):
                alerts.setdefault(name, []).append(rule)

    for name, severity in REQUIRED.items():
        found = alerts.get(name, [])
        if not found:
            problems.append(f"There is no alert named {name}.")
            continue
        if len(found) > 1:
            problems.append(f"{name} is defined {len(found)} times.")
            continue
        rule = found[0]
        labels = rule.get("labels") or {}
        if labels.get("severity") != severity:
            problems.append(f"{name} needs the label severity: {severity}.")
        annotations = rule.get("annotations") or {}
        if not isinstance(annotations, dict):
            problems.append(f"{name} has annotations that are not a mapping.")
            continue
        summary = annotations.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            problems.append(f"{name} has no summary annotation.")
        runbook = annotations.get("runbook_url")
        if not isinstance(runbook, str) or not runbook.startswith("https://"):
            problems.append(f"{name} has no runbook_url annotation with an https:// link.")
    return problems, False


def main() -> int:
    try:
        with open(sys.argv[1], encoding="utf-8") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        print("alerts.yml is not UTF-8 text.")
        return 2
    import yaml

    try:
        doc = load(text)
    except Unsafe as exc:
        print(exc)
        return 2
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        print(f"alerts.yml is not valid YAML{where}.")
        return 2
    problems, unsafe = check(doc)
    for problem in problems:
        print(problem)
    if unsafe:
        return 2
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
