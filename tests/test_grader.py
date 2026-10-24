"""Unit tests for the PR scope rules and the result validation."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gradelib import FileChange, evaluate_scope, load_challenges, match_path, sanitize_detail  # noqa: E402
from report import BadResult, validate  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "challenges" / "fix-the-deployment" / "grade"))
import check_manifests  # noqa: E402

CHALLENGES = load_challenges()
SHA = "a" * 40


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
        for word in ("privileged", "hostPID", "volume type"):
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

    def test_root_and_restricted_rules(self):
        self.assertTrue(check_manifests.check([deployment(securityContext={"runAsUser": 0}), SERVICE]))
        self.assertTrue(check_manifests.check([deployment(securityContext={}), SERVICE]))
        dep = deployment()
        dep["spec"]["template"]["spec"]["securityContext"] = {}
        self.assertTrue(check_manifests.check([dep, SERVICE]))


if __name__ == "__main__":
    unittest.main()
