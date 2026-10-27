"""Unit tests for the PR scope rules and the result validation."""

import importlib.util
import ipaddress
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gradelib import FileChange, evaluate_scope, load_challenges, match_path, sanitize_detail  # noqa: E402
from report import BadResult, validate  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "challenges" / "fix-the-deployment" / "grade"))
import check_manifests  # noqa: E402

CHALLENGES = load_challenges()
SHA = "a" * 40
ROOT = Path(__file__).resolve().parent.parent


def grader_module(challenge, name):
    """Import challenges/<challenge>/grade/<name>.py under a unique module name."""
    folder = ROOT / "challenges" / challenge / "grade"
    sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location(f"{challenge.replace('-', '_')}_{name}", folder / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_rules = grader_module("write-the-alerts", "check_rules")
gen_tests = grader_module("write-the-alerts", "gen_tests")
gen_cases = grader_module("fix-the-network-module", "gen_cases")
check_plan = grader_module("fix-the-network-module", "check_plan")
sqlcheck = grader_module("speed-up-the-queries", "sqlcheck")


def changes(*paths, status="modified"):
    return [FileChange(path=p, status=status) for p in paths]


class ScopeTests(unittest.TestCase):
    def test_solution_file_is_graded(self):
        scope = evaluate_scope(changes("challenges/script-it/summarize.sh"), CHALLENGES)
        self.assertEqual((scope.kind, scope.challenge), ("graded", "script-it"))

    def test_glob_solution_paths(self):
        scope = evaluate_scope(changes("challenges/fix-the-deployment/k8s/extra.yaml"), CHALLENGES)
        self.assertEqual(scope.kind, "graded")
        scope = evaluate_scope(changes("challenges/fix-the-deployment/k8s/sub/extra.yaml"), CHALLENGES)
        self.assertEqual(scope.kind, "not_graded")

    def test_editing_tests_is_rejected(self):
        scope = evaluate_scope(
            changes("challenges/script-it/summarize.sh", "challenges/script-it/grade/summarize.bats"), CHALLENGES
        )
        self.assertEqual((scope.kind, scope.reason), ("rejected", "outside_paths"))
        self.assertEqual(scope.paths, ["challenges/script-it/grade/summarize.bats"])

    def test_editing_the_workflow_is_rejected(self):
        scope = evaluate_scope(
            changes("challenges/shrink-the-image/Dockerfile", ".github/workflows/grade.yml"), CHALLENGES
        )
        self.assertEqual(scope.kind, "rejected")

    def test_challenge_metadata_is_not_a_solution_file(self):
        scope = evaluate_scope(
            changes("challenges/shrink-the-image/Dockerfile", "challenges/shrink-the-image/challenge.json"), CHALLENGES
        )
        self.assertEqual(scope.kind, "rejected")

    def test_unrelated_change_is_not_graded(self):
        scope = evaluate_scope(changes("README.md"), CHALLENGES)
        self.assertEqual(scope.kind, "not_graded")

    def test_two_challenges_are_rejected(self):
        scope = evaluate_scope(
            changes("challenges/shrink-the-image/Dockerfile", "challenges/script-it/summarize.sh"), CHALLENGES
        )
        self.assertEqual((scope.kind, scope.reason), ("rejected", "several_challenges"))

    def test_rename_from_outside_is_rejected(self):
        change = FileChange(
            path="challenges/script-it/summarize.sh", status="renamed", previous_path="challenges/script-it/grade/run.sh"
        )
        self.assertEqual(evaluate_scope([change], CHALLENGES).kind, "rejected")

    def test_too_many_files(self):
        paths = [f"challenges/fix-the-deployment/k8s/f{i}.yaml" for i in range(25)]
        self.assertEqual(evaluate_scope(changes(*paths), CHALLENGES).reason, "too_many_files")

    def test_traversal_paths_are_rejected(self):
        scope = evaluate_scope(changes("challenges/script-it/../script-it/summarize.sh"), CHALLENGES)
        self.assertEqual((scope.kind, scope.reason), ("rejected", "bad_path"))

    def test_match_path(self):
        self.assertTrue(match_path("k8s/*.yaml", "k8s/a.yaml"))
        self.assertFalse(match_path("k8s/*.yaml", "k8s/a/b.yaml"))
        self.assertFalse(match_path("Dockerfile", "dockerfile"))


class ValidateTests(unittest.TestCase):
    def result(self, **over):
        challenge = CHALLENGES["script-it"]
        base = {
            "schema": 1,
            "challenge": "script-it",
            "head_sha": SHA,
            "status": "passed",
            "reason": None,
            "checks": [{"id": c, "status": "pass", "detail": "ok"} for c in challenge.check_ids],
            "graded_at": "2026-10-24T00:00:00Z",
        }
        base.update(over)
        return base

    def test_valid_pass(self):
        trusted = validate(self.result(), CHALLENGES["script-it"], SHA)
        self.assertEqual(trusted["status"], "passed")

    def test_overall_status_is_recomputed(self):
        result = self.result(status="passed")
        result["checks"][0]["status"] = "fail"
        self.assertEqual(validate(result, CHALLENGES["script-it"], SHA)["status"], "failed")

    def test_other_commit_is_refused(self):
        with self.assertRaises(BadResult):
            validate(self.result(head_sha="b" * 40), CHALLENGES["script-it"], SHA)

    def test_other_challenge_is_refused(self):
        with self.assertRaises(BadResult):
            validate(self.result(), CHALLENGES["shrink-the-image"], SHA)

    def test_missing_or_extra_checks_are_refused(self):
        result = self.result()
        result["checks"] = result["checks"][1:]
        with self.assertRaises(BadResult):
            validate(result, CHALLENGES["script-it"], SHA)
        result = self.result()
        result["checks"].append({"id": "bonus", "status": "pass", "detail": ""})
        with self.assertRaises(BadResult):
            validate(result, CHALLENGES["script-it"], SHA)

    def test_unknown_keys_are_refused(self):
        with self.assertRaises(BadResult):
            validate(self.result(pr=1), CHALLENGES["script-it"], SHA)

    def test_details_are_sanitized(self):
        result = self.result()
        result["checks"][0]["detail"] = "@everyone see [this](https://evil.example) <img> `x` | y"
        detail = validate(result, CHALLENGES["script-it"], SHA)["checks"][result["checks"][0]["id"]][1]
        for bad in "@[]<>`|":
            self.assertNotIn(bad, detail)
        self.assertNotIn("://", detail)

    def test_sanitize_length(self):
        self.assertLessEqual(len(sanitize_detail("x" * 5000)), 300)



def deployment(**container_overrides):
    container = {
        "name": "api",
        "image": "devops-daily/inventory-api:1.0",
        "env": [{"name": "STORE_NAME", "valueFrom": {"configMapKeyRef": {"name": "inventory-config", "key": "store-name"}}}],
        "ports": [{"name": "http", "containerPort": 8080}],
        "readinessProbe": {"httpGet": {"path": "/healthz", "port": "http"}},
        "livenessProbe": {"httpGet": {"path": "/healthz", "port": 8080}},
        "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
    }
    container.update(container_overrides)
    pod_security = {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "inventory-api"},
        "spec": {"replicas": 2, "template": {"spec": {"securityContext": pod_security, "containers": [container]}}},
    }


SERVICE = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "inventory-api"}, "spec": {"selector": {"app": "x"}}}


class ManifestTests(unittest.TestCase):
    def test_good_manifests(self):
        self.assertEqual(check_manifests.check([deployment(), SERVICE]), [])

    def test_json_stream_and_lists(self):
        text = '{"kind": "List", "items": [{"kind": "Pod"}]}\n{"kind": "Service"}'
        items = check_manifests.objects(check_manifests.read_documents(text))
        self.assertEqual([i["kind"] for i in items], ["Pod", "Service"])

    def test_privileged_and_host_access_are_refused(self):
        bad = deployment(securityContext={"privileged": True})
        bad["spec"]["template"]["spec"]["hostPID"] = True
        bad["spec"]["template"]["spec"]["volumes"] = [{"name": "root", "hostPath": {"path": "/"}}]
        problems = " ".join(check_manifests.check([bad, SERVICE]))
        for word in ("privileged", "hostPID", "volumes"):
            self.assertIn(word, problems)

    def test_other_kinds_and_images_are_refused(self):
        crb = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "x"}}
        self.assertTrue(check_manifests.check([deployment(), SERVICE, crb]))
        self.assertTrue(check_manifests.check([deployment(image="nginx"), SERVICE]))

    def test_literal_store_name_is_refused(self):
        self.assertTrue(check_manifests.check([deployment(env=[{"name": "STORE_NAME", "value": "riverside"}]), SERVICE]))

    def test_probes_must_be_http_and_prompt(self):
        self.assertTrue(check_manifests.check([deployment(livenessProbe={"tcpSocket": {"port": 8080}}), SERVICE]))
        slow = {"httpGet": {"path": "/healthz", "port": 8080}, "initialDelaySeconds": 3600}
        self.assertTrue(check_manifests.check([deployment(livenessProbe=slow), SERVICE]))
        self.assertTrue(check_manifests.check([deployment(readinessProbe=None), SERVICE]))

    def test_duplicate_env_is_refused(self):
        env = [
            {"name": "STORE_NAME", "valueFrom": {"configMapKeyRef": {"name": "inventory-config", "key": "store-name"}}},
            {"name": "STORE_NAME", "value": "riverside"},
        ]
        self.assertIn("more than once", " ".join(check_manifests.check([deployment(env=env), SERVICE])))

    def test_literal_store_name_beside_env_from_is_refused(self):
        dep = deployment(env=[{"name": "STORE_NAME", "value": "riverside"}], envFrom=[{"configMapRef": {"name": "c"}}])
        self.assertTrue(check_manifests.check([dep, SERVICE]))

    def test_probe_paths_and_ports_are_plain(self):
        for path in ("/healthz -o /tmp/x", "/a\nb", "http://evil/", "/{a,b}", "/x?y=1"):
            probe = {"httpGet": {"path": path, "port": 8080}}
            self.assertTrue(check_manifests.check([deployment(livenessProbe=probe), SERVICE]), path)
        for port in (0, 70000, "nope", True):
            probe = {"httpGet": {"path": "/healthz", "port": port}}
            self.assertTrue(check_manifests.check([deployment(livenessProbe=probe), SERVICE]), port)

    def test_app_cannot_be_replaced(self):
        self.assertTrue(check_manifests.check([deployment(command=["/bin/sh"]), SERVICE]))
        self.assertTrue(check_manifests.check([deployment(volumeMounts=[{"name": "b", "mountPath": "/inventory-api"}]), SERVICE]))
        cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "b"}, "binaryData": {"server": "AAAA"}}
        self.assertTrue(check_manifests.check([deployment(), SERVICE, cm]))

    def test_root_and_restricted_rules(self):
        self.assertTrue(check_manifests.check([deployment(securityContext={"runAsUser": 0}), SERVICE]))
        self.assertTrue(check_manifests.check([deployment(securityContext={}), SERVICE]))
        dep = deployment()
        dep["spec"]["template"]["spec"]["securityContext"] = {}
        self.assertTrue(check_manifests.check([dep, SERVICE]))


class NewChallengeScopeTests(unittest.TestCase):
    def scope(self, *paths):
        return evaluate_scope(changes(*paths), CHALLENGES)

    def test_solution_files_are_graded(self):
        for path, cid in (
            ("challenges/write-the-alerts/alerts.yml", "write-the-alerts"),
            ("challenges/fix-the-network-module/variables.tf", "fix-the-network-module"),
            ("challenges/speed-up-the-queries/queries/revenue_for_day.sql", "speed-up-the-queries"),
            ("challenges/speed-up-the-queries/indexes.sql", "speed-up-the-queries"),
        ):
            self.assertEqual((self.scope(path).kind, self.scope(path).challenge), ("graded", cid), path)

    def test_tests_and_fixed_files_are_not_solution_files(self):
        for extra in (
            "challenges/write-the-alerts/tests/example.test.yml",
            "challenges/write-the-alerts/grade/gen_tests.py",
            "challenges/fix-the-network-module/tests/network.tftest.hcl",
            "challenges/fix-the-network-module/versions.tf",
            "challenges/fix-the-network-module/main_override.tf",
            "challenges/fix-the-network-module/override.tf",
            "challenges/fix-the-network-module/.terraform.lock.hcl",
            "challenges/fix-the-network-module/terraform.tfvars",
            "challenges/speed-up-the-queries/schema.sql",
            "challenges/speed-up-the-queries/seed.sql",
            "challenges/speed-up-the-queries/queries/extra.sql",
        ):
            cid = extra.split("/")[1]
            solution = CHALLENGES[cid].solution_paths[0]
            scope = self.scope(f"challenges/{cid}/{solution}", extra)
            self.assertEqual((scope.kind, scope.reason), ("rejected", "outside_paths"), extra)


REFERENCE_RULES = {
    "groups": [{"name": "checkout", "rules": [
        {"alert": name, "expr": "vector(1)", "for": "1m", "labels": {"severity": severity},
         "annotations": {"summary": "s", "runbook_url": "https://runbooks.example.com/x"}}
        for name, severity in check_rules.REQUIRED.items()
    ]}]
}


def rules(mutate):
    doc = json.loads(json.dumps(REFERENCE_RULES))
    mutate(doc)
    return check_rules.check(doc)


class AlertRuleShapeTests(unittest.TestCase):
    def test_complete_rules(self):
        self.assertEqual(check_rules.check(REFERENCE_RULES), ([], False))

    def test_reserved_series_and_labels_stop_grading(self):
        def record_alerts(doc):
            doc["groups"][0]["rules"].append({"record": "ALERTS", "expr": "vector(1)"})

        def set_alertstate(doc):
            doc["groups"][0]["rules"][0]["labels"]["alertstate"] = "firing"

        def group_alertname(doc):
            doc["groups"][0]["labels"] = {"alertname": "CheckoutInstanceDown"}

        for mutate in (record_alerts, set_alertstate, group_alertname):
            self.assertTrue(rules(mutate)[1], mutate.__name__)

    def test_missing_labels_and_annotations_are_listed(self):
        def drop(doc):
            first = doc["groups"][0]["rules"][0]
            first["labels"]["severity"] = "ticket"
            del first["annotations"]["runbook_url"]
            doc["groups"][0]["rules"].append(dict(doc["groups"][0]["rules"][1]))

        problems, unsafe = rules(drop)
        self.assertFalse(unsafe)
        text = " ".join(problems)
        for word in ("severity: page", "runbook_url", "defined 2 times"):
            self.assertIn(word, text)

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML is not installed")
    def test_yaml_aliases_and_tags_are_refused(self):
        for text in ("a: &x [1, 2]\nb: *x\n", "a: !!python/name:os.system x\n", "a: !custom 1\n"):
            with self.assertRaises(check_rules.Unsafe):
                check_rules.load(text)


class AlertScenarioTests(unittest.TestCase):
    def generate(self, seed):
        out = Path(tempfile.mkdtemp())
        sys.argv = ["gen_tests.py", "--out", str(out), "--rules", "/rules/alerts.yml", "--seed", str(seed)]
        gen_tests.main()
        return out, json.loads((out / "scenarios.json").read_text())

    def test_one_scenario_per_file_for_every_check(self):
        out, scenarios = self.generate(7)
        self.assertEqual({s["check"] for s in scenarios}, set(gen_tests.CHECKS))
        for scenario in scenarios:
            doc = json.loads((out / scenario["file"]).read_text())
            self.assertEqual(doc["rule_files"], ["/rules/alerts.yml"])
            self.assertEqual(len(doc["tests"]), 1)
            self.assertTrue(scenario["description"])

    def test_series_are_random(self):
        first = self.generate(1)[0] / "instance-down-1.yml"
        second = self.generate(2)[0] / "instance-down-1.yml"
        self.assertNotEqual(first.read_text(), second.read_text())


AZ_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]+[a-f]$")
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]


def valid_inputs(case):
    """The input rules of the network module's README, written out in Python."""
    try:
        vpc = ipaddress.ip_network(case["vpc_cidr"])
    except ValueError:
        return False
    azs = case["azs"]
    return (
        re.fullmatch(r"[a-z][a-z0-9-]{1,18}[a-z0-9]", case["name"]) is not None
        and case["environment"] in ("dev", "staging", "prod")
        and vpc.version == 4 and "/" in case["vpc_cidr"] and 16 <= vpc.prefixlen <= 24
        and any(vpc.subnet_of(p) for p in PRIVATE)
        and 2 <= len(azs) <= 4 and len(set(azs)) == len(azs)
        and all(AZ_RE.match(az) for az in azs) and len({az[:-1] for az in azs}) == 1
    )


class NetworkModuleTests(unittest.TestCase):
    def test_az_letter_picks_the_block(self):
        case = {"name": "shop", "environment": "prod", "vpc_cidr": "10.20.0.0/16",
                "azs": ["eu-west-1b", "eu-west-1a"], "tags": {"Name": "mine", "Team": "x"}}
        exp = gen_cases.expected(case)
        self.assertEqual(exp["subnets"]["public/eu-west-1b"]["cidr"], "10.20.16.0/20")
        self.assertEqual(exp["subnets"]["private/eu-west-1b"]["cidr"], "10.20.144.0/20")
        self.assertEqual(exp["subnets"]["private/eu-west-1a"]["cidr"], "10.20.128.0/20")
        self.assertEqual(exp["vpc"]["tags"], {"Name": "shop-prod", "Team": "x", "Environment": "prod",
                                              "ManagedBy": "terraform"})

    def test_generated_cases_follow_the_rules(self):
        import random
        for seed in range(40):
            gen = gen_cases.Gen(random.Random(seed))
            self.assertTrue(valid_inputs(gen.good()), seed)
            for variable, make in (("name", gen.bad_name), ("environment", gen.bad_environment),
                                   ("vpc_cidr", gen.bad_vpc_cidr), ("azs", gen.bad_azs)):
                case = gen.good()
                case[variable] = make()[0]
                self.assertFalse(valid_inputs(case), (seed, variable, case[variable]))

    def show(self, case, mutate=None):
        exp = gen_cases.expected(case)
        resources = [{"address": "aws_vpc.this", "mode": "managed", "type": "aws_vpc", "name": "this",
                      "values": {"cidr_block": exp["vpc"]["cidr"], "enable_dns_support": True,
                                 "enable_dns_hostnames": True, "tags": exp["vpc"]["tags"]}}]
        outputs, ids = {}, {}
        for key, sub in exp["subnets"].items():
            tier, az = key.split("/")
            resources.append({"address": f'aws_subnet.{tier}["{az}"]', "mode": "managed", "type": "aws_subnet",
                              "name": tier, "index": az,
                              "values": {"availability_zone": az, "cidr_block": sub["cidr"],
                                         "map_public_ip_on_launch": sub["public_ip"], "tags": sub["tags"]}})
            outputs.setdefault(f"{tier}_subnet_cidrs", {})[az] = sub["cidr"]
            ids.setdefault(f"{tier}_subnet_ids", {})[az] = True
        show = {"planned_values": {"root_module": {"resources": resources},
                                   "outputs": {k: {"value": v} for k, v in outputs.items()}},
                "output_changes": {"vpc_id": {"after_unknown": True},
                                   **{k: {"after_unknown": v} for k, v in ids.items()}}}
        show = json.loads(json.dumps(show))
        if mutate:
            mutate(show)
        folder = Path(tempfile.mkdtemp())
        (folder / "show.json").write_text(json.dumps(show))
        (folder / "answers.json").write_text(json.dumps({"c": {"kind": "good", "inputs": case, "expected": exp}}))
        return check_plan.cmd_compare(str(folder / "show.json"), str(folder / "answers.json"), "c")

    CASE = {"name": "orders", "environment": "dev", "vpc_cidr": "192.168.4.0/24",
            "azs": ["us-east-1f", "us-east-1d"], "tags": {}}

    def test_matching_plan_passes(self):
        self.assertEqual(self.show(self.CASE), {"subnets": [], "tags": []})

    def test_wrong_ranges_tags_and_count_keys_fail(self):
        def overlap(show):
            show["planned_values"]["root_module"]["resources"][2]["values"]["cidr_block"] = "192.168.4.0/28"

        def tags(show):
            show["planned_values"]["root_module"]["resources"][0]["values"]["tags"]["Name"] = "x"

        def count(show):
            for r in show["planned_values"]["root_module"]["resources"][1:]:
                r["index"] = 0

        def known_ids(show):
            show["output_changes"]["public_subnet_ids"] = {"after": {"us-east-1d": "x", "us-east-1f": "y"},
                                                           "after_unknown": {"us-east-1d": False, "us-east-1f": False}}

        self.assertTrue(self.show(self.CASE, known_ids)["subnets"])
        self.assertTrue(self.show(self.CASE, overlap)["subnets"])
        self.assertTrue(self.show(self.CASE, tags)["tags"])
        self.assertTrue(self.show(self.CASE, count)["subnets"])

    def test_only_validation_rules_count_as_rejections(self):
        folder = Path(tempfile.mkdtemp())
        validation = {"type": "diagnostic", "diagnostic": {"severity": "error", "summary": "Invalid value for variable",
                                                           "detail": "x\n\nThis was checked by the validation rule at variables.tf:3,3-13."}}
        other = {"type": "diagnostic", "diagnostic": {"severity": "error", "summary": "Invalid function argument"}}
        (folder / "a.jsonl").write_text(json.dumps(validation) + "\n")
        (folder / "b.jsonl").write_text(json.dumps(other) + "\nnot json\n")
        self.assertTrue(check_plan.cmd_diagnostics(str(folder / "a.jsonl"))["validation"])
        self.assertFalse(check_plan.cmd_diagnostics(str(folder / "b.jsonl"))["validation"])


class QueryGateTests(unittest.TestCase):
    def test_starter_queries_pass_the_gate(self):
        for path in (ROOT / "challenges" / "speed-up-the-queries" / "queries").glob("*.sql"):
            self.assertIsNotNone(sqlcheck.plain_query(path.read_text()), path.name)

    def test_one_select_only(self):
        ok = ("SELECT 1", "with a as (select 1) select * from a;", "-- note; with a semicolon\nSELECT 1",
              "/* c */ (SELECT 1)")
        bad = ("", "SET enable_seqscan = off; SELECT 1", "SELECT 1; SELECT 2", "DELETE FROM orders",
               "-- c\nUPDATE orders SET status = 'x'", "EXPLAIN SELECT 1", "SELECT 1 -- x\n; SET a = 1",
               "SELECT 'a" + "x" * 5000 + "'")
        for query in ok:
            self.assertIsNotNone(sqlcheck.plain_query(query), query)
        for query in bad:
            self.assertIsNone(sqlcheck.plain_query(query), query)

    def test_pages_and_seq_scans_are_read_from_the_whole_plan(self):
        plan = {"Node Type": "Limit", "Shared Hit Blocks": 30, "Shared Read Blocks": 4, "Temp Written Blocks": 2,
                "Plans": [{"Node Type": "Sort", "Plans": [{"Node Type": "Seq Scan", "Relation Name": "orders"}]},
                          {"Node Type": "Seq Scan", "Relation Name": "pg_class"}]}
        self.assertEqual(sqlcheck.plan_pages(plan), 36)
        self.assertEqual(sqlcheck.seq_scans(plan), ["orders"])


if __name__ == "__main__":
    unittest.main()
