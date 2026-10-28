"""Generate random inputs for the network module, and the expected results.

  python3 gen_cases.py --cases DIR --answers FILE [--seed N]

Writes one <id>.tfvars.json per case to DIR, and FILE with what each case
expects. The grader mounts a single tfvars file into the container that plans
the module; the answers never go into a container.

- "good" cases: valid inputs. The plan must succeed and match the expected
  VPC, subnets, outputs and tags.
- "bad" cases: valid inputs except one variable, which breaks one rule from
  the README. There is one for every rule, with a random value each time.
  A validation rule must reject the plan.

Values never sit on the edge of a rule the README leaves open, so a correct
module always passes.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import random
import secrets
import string
from pathlib import Path

REGIONS = ["eu-west-1", "eu-west-2", "eu-central-1", "eu-north-1", "us-east-1", "us-east-2",
           "us-west-2", "ca-central-1", "ap-southeast-2", "ap-northeast-1", "sa-east-1"]
LETTERS = "abcdef"
PRIVATE = [ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
           ipaddress.ip_network("192.168.0.0/16")]
EXTRA_TAGS = ["Team", "CostCenter", "Owner", "Project", "Service"]
MODULE_TAGS = ["Name", "Environment", "ManagedBy", "Tier"]
WORDS = ["shop", "orders", "billing", "search", "edge", "data", "core", "api", "web", "batch", "ml", "auth"]


class Gen:
    def __init__(self, rng: random.Random):
        self.rng = rng

    # --- good values -------------------------------------------------------

    def name(self) -> str:
        r = self.rng
        while True:
            parts = [r.choice(WORDS) for _ in range(r.randint(1, 3))]
            if r.random() < 0.4:
                parts.append(str(r.randint(1, 99)))
            name = "-".join(parts)
            if 3 <= len(name) <= 20:
                return name

    def environment(self) -> str:
        return self.rng.choice(["dev", "staging", "prod"])

    def vpc_cidr(self) -> str:
        r = self.rng
        base = r.choice(PRIVATE)
        prefix = r.randint(16, 24)
        count = 2 ** (prefix - base.prefixlen)
        network = ipaddress.ip_network((int(base.network_address) + r.randrange(count) * 2 ** (32 - prefix), prefix))
        return str(network)

    def azs(self) -> list[str]:
        region = self.rng.choice(REGIONS)
        letters = self.rng.sample(LETTERS, self.rng.randint(2, 4))
        return [f"{region}{letter}" for letter in letters]

    def tags(self) -> dict[str, str]:
        r = self.rng
        keys = r.sample(EXTRA_TAGS, r.randint(0, 3))
        tags = {k: r.choice(WORDS) + "-" + "".join(r.choices(string.ascii_lowercase, k=4)) for k in keys}
        if r.random() < 0.6:
            tags[r.choice(MODULE_TAGS)] = "set-by-caller"
        return tags

    def good(self) -> dict:
        return {"name": self.name(), "environment": self.environment(), "vpc_cidr": self.vpc_cidr(),
                "azs": self.azs(), "tags": self.tags()}

    # --- bad values: every rule of the README, a random value for each ---------

    def bad_names(self) -> list[tuple[str, str]]:
        r = self.rng
        good = self.name()
        return [
            (good[0].upper() + good[1:], "has an upper-case letter"),
            (r.choice(["", r.choice(string.ascii_lowercase), "".join(r.choices(string.ascii_lowercase, k=2))]),
             "is shorter than 3 characters"),
            (good + "-" + "".join(r.choices(string.ascii_lowercase, k=21 - len(good))), "is longer than 20 characters"),
            (r.choice([str(r.randint(1, 9)), "-"]) + good, "does not start with a letter"),
            (good + "-", "ends with a hyphen"),
            (good.replace("-", r.choice(["_", " ", "."])) if "-" in good else good + r.choice(["_x", " x", ".x"]),
             "has a character other than a letter, digit or hyphen"),
        ]

    def bad_environments(self) -> list[tuple[str, str]]:
        r = self.rng
        return [
            (r.choice(["production", "development", "test", "stage", "qa", "live"]), "is not dev, staging or prod"),
            (r.choice(["Prod", "PROD", "Dev", "Staging", "dev ", " prod"]), "is not exactly dev, staging or prod"),
        ]

    def bad_vpc_cidrs(self) -> list[tuple[str, str]]:
        r = self.rng
        a, b = r.randint(0, 255), r.randint(0, 255)
        return [
            (f"{r.choice([3, 8, 34, 52, 100, 151, 185, 203])}.{a}.0.0/16", "is a public range"),
            (r.choice([f"172.{r.randint(32, 63)}.0.0/16", f"172.{r.randint(0, 15)}.0.0/16",
                       f"192.{r.choice([167, 169, 170])}.{a}.0/24", f"11.{a}.0.0/16"]),
             "is just outside a private range"),
            (r.choice([f"10.0.0.0/{r.randint(8, 15)}", "172.16.0.0/12", "192.168.0.0/15"]), "has a prefix shorter than /16"),
            (f"10.{a}.{b}.0/{r.randint(25, 30)}", "has a prefix longer than /24"),
            (r.choice([f"10.{a}.0.0", f"10.{a}.0.0/33", f"10.{a}.0.300/16", "banana", "default", "10.20.0.0-10.20.255.255", ""]),
             "is not a CIDR block"),
            (f"fd{a:02x}:{b:02x}00::/48", "is an IPv6 block"),
        ]

    def bad_azs(self) -> list[tuple[list[str], str]]:
        r = self.rng
        region = r.choice(REGIONS)
        other = r.choice([x for x in REGIONS if x != region])
        letters = r.sample(LETTERS, 6)
        return [
            ([f"{region}{r.choice(LETTERS)}"], "lists one AZ"),
            ([f"{region}{c}" for c in letters[:r.randint(5, 6)]], "lists more than four AZs"),
            ([f"{region}{letters[0]}", f"{region}{letters[1]}", f"{region}{letters[0]}"], "lists an AZ twice"),
            ([f"{region}{letters[0]}", f"{other}{letters[1]}"], "has AZs in two regions"),
            ([f"{region}{letters[0]}", f"{region}{r.choice('ghjk')}"], "has an AZ letter after f"),
            (r.choice([[f"{region}{letters[0]}", region],
                       [f"{region}{letters[0]}".upper(), f"{region}{letters[1]}".upper()],
                       [f"{region}{letters[0]}".replace("-", "_"), f"{region}{letters[1]}".replace("-", "_")]]),
             "has an entry that is not an AZ name"),
        ]


def expected(case: dict) -> dict:
    vpc = ipaddress.ip_network(case["vpc_cidr"])
    blocks = list(vpc.subnets(new_prefix=vpc.prefixlen + 4))
    prefix = f"{case['name']}-{case['environment']}"
    caller = case.get("tags", {})
    module = {"Environment": case["environment"], "ManagedBy": "terraform"}
    subnets = {}
    for az in case["azs"]:
        n = LETTERS.index(az[-1])
        for tier, block in (("public", n), ("private", n + 8)):
            subnets[f"{tier}/{az}"] = {
                "az": az,
                "cidr": str(blocks[block]),
                "public_ip": tier == "public",
                "tags": {**caller, **module, "Name": f"{prefix}-{tier}-{az}", "Tier": tier},
            }
    return {
        "vpc": {"cidr": case["vpc_cidr"], "tags": {**caller, **module, "Name": prefix}},
        "subnets": subnets,
    }


def hcl(value) -> str:
    """A value written as HCL. JSON strings and lists are valid HCL."""
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {hcl(v)}" for k, v in value.items()) + " }"
    return json.dumps(value)


def wiring_test(gen: Gen) -> str:
    """A terraform test file with random mocked IDs, checking which ID goes where.

    IDs only exist after apply, so a plan cannot show whether the outputs hold
    subnet IDs or whether each subnet is in aws_vpc.this. This test gives the
    VPC and every subnet a random ID during plan and checks both. The IDs are
    new for every run and only this file knows them; the module may not read
    files (see hclscan.py).
    """
    case = gen.good()
    hexid = lambda n: "".join(gen.rng.choices("0123456789abcdef", k=n))  # noqa: E731
    vpc = f"vpc-0{hexid(16)}"
    ids = {tier: {az: f"subnet-0{hexid(16)}" for az in case["azs"]} for tier in ("public", "private")}
    lines = ['mock_provider "aws" {}', "", 'run "wiring" {', "  command = plan", "", "  variables {"]
    lines += [f"    {k} = {hcl(v)}" for k, v in case.items()]
    lines += ["  }", ""]
    targets = [("aws_vpc.this", vpc)] + [(f"aws_subnet.{tier}[{json.dumps(az)}]", sid)
                                         for tier in ids for az, sid in ids[tier].items()]
    for target, rid in targets:
        lines += ["  override_resource {", f"    target          = {target}", "    override_during = plan",
                  f"    values          = {{ id = {json.dumps(rid)} }}", "  }", ""]
    asserts = [
        (f"output.vpc_id == {json.dumps(vpc)}", "vpc_id"),
        (f"alltrue([for s in concat(values(aws_subnet.public), values(aws_subnet.private)) : s.vpc_id == {json.dumps(vpc)}])",
         "subnets"),
        (f"output.public_subnet_ids == {hcl(ids['public'])}", "public_subnet_ids"),
        (f"output.private_subnet_ids == {hcl(ids['private'])}", "private_subnet_ids"),
    ]
    for condition, name in asserts:
        lines += ["  assert {", f"    condition     = {condition}", f"    error_message = {json.dumps(name)}", "  }", ""]
    lines[-1] = "}"
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True)
    parser.add_argument("--answers", required=True)
    parser.add_argument("--good", type=int, default=3)
    parser.add_argument("--wiring", help="also write a terraform test file with random mocked IDs here")
    parser.add_argument("--seed", type=int, help="for repeatable local runs; grading uses a random seed")
    args = parser.parse_args()
    rng = random.Random(args.seed if args.seed is not None else secrets.randbits(64))
    gen = Gen(rng)
    cases_dir = Path(args.cases)
    cases_dir.mkdir(parents=True, exist_ok=True)

    cases = []
    for n in range(args.good):
        case = gen.good()
        if n == 0:
            del case["tags"]  # tags is optional: the module must work without it
        elif n == 1:
            # The caller sets every tag the module sets itself; the module wins.
            case["tags"].update({key: "set-by-caller" for key in MODULE_TAGS})
        cases.append({"kind": "good", "inputs": case, "expected": expected(case)})
    for variable, make in (("name", gen.bad_names), ("environment", gen.bad_environments),
                           ("vpc_cidr", gen.bad_vpc_cidrs), ("azs", gen.bad_azs)):
        for value, why in make():
            inputs = gen.good()
            inputs[variable] = value
            cases.append({"kind": "bad", "inputs": inputs, "variable": variable,
                          "why": f"{variable} {json.dumps(value)} {why}"})
    rng.shuffle(cases)

    answers = {}
    for case in cases:
        # A random file name, so nothing about a case shows in its name.
        cid = secrets.token_hex(6) if args.seed is None else "%012x" % rng.getrandbits(48)
        (cases_dir / f"{cid}.tfvars.json").write_text(json.dumps(case["inputs"], indent=2) + "\n", encoding="utf-8")
        answers[cid] = case
    Path(args.answers).write_text(json.dumps(answers, indent=2) + "\n", encoding="utf-8")
    if args.wiring:
        Path(args.wiring).write_text(wiring_test(gen), encoding="utf-8")


if __name__ == "__main__":
    main()
