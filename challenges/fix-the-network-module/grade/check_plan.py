"""Read what Terraform printed for the submitted module, as untrusted data.

  check_plan.py validate VALIDATE_JSON        first error of terraform validate -json, if any
  check_plan.py tests TEST_JSONL              runs of terraform test -json: total and failed
  check_plan.py diagnostics PLAN_JSONL        whether a plan failed on a variable validation rule
  check_plan.py compare SHOW_JSON ANSWERS ID  compare a planned module with the expected one
  check_plan.py score OUT_DIR ANSWERS         results of the inputs, subnets and tags checks

Terraform wrote these files, but the submission controls names, values and
messages inside them. Everything is parsed as JSON, size-limited, and only
fixed fields are read. Each command prints one JSON object for grade.sh.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_BYTES = 32 * 1024 * 1024


def read(path: str) -> str:
    with open(path, "rb") as fh:
        data = fh.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise SystemExit(f"{path} is larger than {MAX_BYTES} bytes")
    return data.decode("utf-8", errors="replace")


def json_lines(path: str) -> list[dict]:
    out = []
    for line in read(path).splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def where(diag: dict) -> str:
    rng = diag.get("range") or {}
    name, line = rng.get("filename"), (rng.get("start") or {}).get("line")
    if isinstance(name, str) and isinstance(line, int) and name in ("main.tf", "variables.tf", "outputs.tf", "versions.tf"):
        return f" ({name} line {line})"
    return ""


def describe(diag: dict) -> str:
    summary = diag.get("summary")
    summary = summary if isinstance(summary, str) else "error"
    return summary[:120] + where(diag)


def cmd_validate(path: str) -> dict:
    try:
        doc = json.loads(read(path))
    except ValueError:
        return {"valid": False, "count": 1, "first": "terraform validate printed no JSON"}
    if not isinstance(doc, dict):
        return {"valid": False, "count": 1, "first": "terraform validate printed no JSON"}
    errors = [d for d in doc.get("diagnostics") or [] if isinstance(d, dict) and d.get("severity") == "error"]
    return {"valid": doc.get("valid") is True and not errors, "count": len(errors),
            "first": describe(errors[0]) if errors else ""}


def cmd_tests(path: str) -> dict:
    status: dict[str, str] = {}
    errors = []
    for msg in json_lines(path):
        run = msg.get("test_run")
        if msg.get("type") == "test_run" and isinstance(run, dict) and isinstance(run.get("run"), str):
            if isinstance(run.get("status"), str):
                status[run["run"]] = run["status"]
        elif msg.get("type") == "diagnostic" and isinstance(msg.get("diagnostic"), dict):
            if msg["diagnostic"].get("severity") == "error":
                errors.append(describe(msg["diagnostic"]))
    failed = sorted(name for name, s in status.items() if s != "pass")
    return {"total": len(status), "failed": failed, "first_error": errors[0] if errors else ""}


def cmd_diagnostics(path: str) -> dict:
    validation = False
    errors = []
    for msg in json_lines(path):
        diag = msg.get("diagnostic")
        if msg.get("type") != "diagnostic" or not isinstance(diag, dict) or diag.get("severity") != "error":
            continue
        detail = diag.get("detail") if isinstance(diag.get("detail"), str) else ""
        if diag.get("summary") == "Invalid value for variable" and "This was checked by the validation rule at" in detail:
            validation = True
        errors.append(describe(diag))
    return {"validation": validation, "errors": errors[:3]}


def root_resources(show: dict) -> list[dict]:
    root = ((show.get("planned_values") or {}).get("root_module") or {})
    return [r for r in root.get("resources") or [] if isinstance(r, dict) and r.get("mode") == "managed"]


def output_value(show: dict, name: str):
    out = ((show.get("planned_values") or {}).get("outputs") or {}).get(name)
    return out.get("value") if isinstance(out, dict) else None


def output_keys(show: dict, name: str):
    """Keys of an output map whose values are unknown until apply (subnet IDs)."""
    change = (show.get("output_changes") or {}).get(name)
    if not isinstance(change, dict):
        return None
    keys: set[str] = set()
    found = False
    for part in (change.get("after"), change.get("after_unknown")):
        if isinstance(part, dict):
            keys |= set(part)
            found = True
    return sorted(keys) if found else None


def short(text: str) -> str:
    return text if len(text) <= 60 else text[:57] + "..."


def cmd_compare(show_path: str, answers_path: str, cid: str) -> dict:
    case = json.loads(read(answers_path))[cid]
    inputs, exp = case["inputs"], case["expected"]
    label = f"vpc_cidr {inputs['vpc_cidr']}, azs {json.dumps(inputs['azs'])}"
    subnets: list[str] = []
    tags: list[str] = []
    try:
        show = json.loads(read(show_path))
    except ValueError:
        return {"subnets": [f"For {label}: terraform show printed no JSON."], "tags": ["Not checked: no plan."]}
    if not isinstance(show, dict):
        return {"subnets": [f"For {label}: terraform show printed no JSON."], "tags": ["Not checked: no plan."]}

    resources = root_resources(show)
    vpcs = [r for r in resources if r.get("type") == "aws_vpc"]
    if len(vpcs) != 1 or vpcs[0].get("address") != "aws_vpc.this":
        subnets.append(f"For {label}: the plan must have one VPC, aws_vpc.this; it has {len(vpcs)} VPC(s).")
    else:
        values = vpcs[0].get("values") or {}
        if values.get("cidr_block") != exp["vpc"]["cidr"]:
            subnets.append(f"For {label}: aws_vpc.this has cidr_block {short(str(values.get('cidr_block')))}.")
        if values.get("enable_dns_support") is not True or values.get("enable_dns_hostnames") is not True:
            subnets.append(f"For {label}: aws_vpc.this does not turn on DNS support and DNS hostnames.")
        if values.get("tags") != exp["vpc"]["tags"]:
            tags.append(f"For {label}: aws_vpc.this has tags {short(json.dumps(values.get('tags'), sort_keys=True))}.")

    planned = {}
    for r in resources:
        if r.get("type") != "aws_subnet":
            continue
        if r.get("name") in ("public", "private") and isinstance(r.get("index"), str):
            planned[f"{r['name']}/{r['index']}"] = r
        else:
            subnets.append(f"For {label}: {short(str(r.get('address')))} is not keyed by AZ name in aws_subnet.public or aws_subnet.private.")
    want = exp["subnets"]
    missing = sorted(set(want) - set(planned))
    extra = sorted(set(planned) - set(want))
    if missing:
        names = ", ".join(f"aws_subnet.{k.split('/')[0]} for {k.split('/')[1]}" for k in missing[:2])
        subnets.append(f"For {label}: the plan has no {names}.")
    if extra:
        subnets.append(f"For {label}: the plan has subnets for AZs that are not in azs.")
    for key in sorted(set(want) & set(planned)):
        tier, az = key.split("/")
        values = planned[key].get("values") or {}
        address = f"aws_subnet.{tier} for {az}"
        if values.get("availability_zone") != az:
            subnets.append(f"For {label}: {address} is in availability zone {short(str(values.get('availability_zone')))}.")
        if values.get("cidr_block") != want[key]["cidr"]:
            subnets.append(f"For {label}: {address} has cidr_block {short(str(values.get('cidr_block')))}, "
                           f"expected {want[key]['cidr']}.")
        if values.get("map_public_ip_on_launch") is not want[key]["public_ip"]:
            subnets.append(f"For {label}: {address} has map_public_ip_on_launch {short(json.dumps(values.get('map_public_ip_on_launch')))}.")
        if values.get("tags") != want[key]["tags"]:
            tags.append(f"For {label}: {address} has tags {short(json.dumps(values.get('tags'), sort_keys=True))}.")

    for tier in ("public", "private"):
        cidrs = {v["az"]: v["cidr"] for k, v in want.items() if k.startswith(tier + "/")}
        if output_value(show, f"{tier}_subnet_cidrs") != cidrs:
            subnets.append(f"For {label}: output {tier}_subnet_cidrs is not a map of the {tier} subnet ranges by AZ.")
        if output_keys(show, f"{tier}_subnet_ids") != sorted(cidrs):
            subnets.append(f"For {label}: output {tier}_subnet_ids is not a map of subnet IDs by AZ.")
    if "vpc_id" not in ((show.get("output_changes") or {})):
        subnets.append(f"For {label}: there is no vpc_id output.")
    return {"subnets": subnets, "tags": tags}


def first(problems: list[str], what: str) -> str:
    return f"{len(problems)} {what}. First: {problems[0]}"


def cmd_score(out_dir: str, answers_path: str) -> dict:
    """Results of the inputs, subnets and tags checks from all case outputs."""
    out = Path(out_dir)
    answers = json.loads(read(answers_path))
    bad_missed, good_rejected, subnet_problems, tag_problems = [], [], [], []
    planned_good = 0
    bad_total = sum(1 for c in answers.values() if c["kind"] == "bad")
    good_total = len(answers) - bad_total
    for cid, case in sorted(answers.items()):
        rc_file = out / f"tf-{cid}.rc"
        rc = rc_file.read_text().strip() if rc_file.is_file() else "missing"
        diags = cmd_diagnostics(str(out / f"tf-{cid}.plan.jsonl")) if (out / f"tf-{cid}.plan.jsonl").is_file() else {"validation": False, "errors": []}
        if case["kind"] == "bad":
            if rc == "0":
                bad_missed.append(f"{case['why']}, but the plan succeeded.")
            elif not diags["validation"]:
                err = diags["errors"][0] if diags["errors"] else "no error message"
                bad_missed.append(f"{case['why']}; the plan failed, but not on a validation rule: {err}.")
            continue
        inputs = case["inputs"]
        label = f"name {inputs['name']}, environment {inputs['environment']}, vpc_cidr {inputs['vpc_cidr']}, azs {json.dumps(inputs['azs'])}"
        if rc != "0":
            err = diags["errors"][0] if diags["errors"] else "no error message"
            good_rejected.append(f"the plan failed for good inputs ({label}): {err}.")
            continue
        planned_good += 1
        result = cmd_compare(str(out / f"tf-{cid}.show.json"), answers_path, cid)
        subnet_problems += result["subnets"]
        tag_problems += result["tags"]

    checks = {}
    if not bad_missed and not good_rejected:
        checks["inputs"] = ("pass", f"All {bad_total} random bad inputs were rejected by a validation rule, "
                                    f"and all {good_total} sets of good inputs planned.")
    elif bad_missed:
        checks["inputs"] = ("fail", first(bad_missed, f"of {bad_total} bad inputs got through"))
    else:
        checks["inputs"] = ("fail", first(good_rejected, f"of {good_total} sets of good inputs failed to plan"))
    if planned_good == 0:
        checks["subnets"] = ("fail", "None of the good input sets planned, so there was nothing to check.")
        checks["tags"] = ("fail", "None of the good input sets planned, so there was nothing to check.")
    else:
        checks["subnets"] = ("pass", f"The VPC, subnets and outputs match for {planned_good} random input sets.") \
            if not subnet_problems else ("fail", first(subnet_problems, "problems"))
        checks["tags"] = ("pass", f"Every resource has the right tags for {planned_good} random input sets.") \
            if not tag_problems else ("fail", first(tag_problems, "problems"))
    return {k: {"status": v[0], "detail": v[1]} for k, v in checks.items()}


def main() -> int:
    cmd, *args = sys.argv[1:]
    if cmd == "validate":
        result = cmd_validate(*args)
    elif cmd == "tests":
        result = cmd_tests(*args)
    elif cmd == "diagnostics":
        result = cmd_diagnostics(*args)
    elif cmd == "compare":
        result = cmd_compare(*args)
    elif cmd == "score":
        result = cmd_score(*args)
    else:
        raise SystemExit(f"unknown command {cmd}")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
