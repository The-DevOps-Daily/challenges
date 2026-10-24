"""Unit tests for the PR scope rules and the result validation."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gradelib import FileChange, evaluate_scope, load_challenges, match_path, sanitize_detail  # noqa: E402
from report import BadResult, validate  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
