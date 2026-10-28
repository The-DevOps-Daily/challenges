# Contributing

Thanks for helping. There are two kinds of PRs in this repo, and the grader treats them differently:

- **Submissions** change only one challenge's solution files. The grader grades them and closes them when they pass. Submissions are never merged.
- **Changes to the repo** (a new challenge, a fix to a grader, docs) touch other files. The grader leaves a note that the PR is not a submission, and a maintainer reviews it.

## Propose a challenge

Open an issue with the **Challenge proposal** template first, before you write code. A good challenge:

- starts from something real: a ticket you could get at work, not a quiz;
- has a broken or unfinished starting point that runs on a laptop and on a GitHub-hosted `ubuntu-24.04` runner, with no cloud account and no paid service;
- can be graded by checks that say what is wrong without saying how to fix it;
- cannot be passed by hardcoding an answer (use random inputs where you can);
- takes 20 to 60 minutes for someone at the stated level.

## Build a challenge

Once a maintainer agrees on the proposal, a challenge is a folder in `challenges/<id>/`:

```text
challenges/<id>/
  README.md        the brief: situation, task, checks, solution files, hints, local testing, how to submit
  challenge.json   id, title, summary, difficulty, minutes, tags, solution_paths and checks
  ...              the starter files: the broken or unfinished thing
  grade/
    grade.sh       runs the checks and calls `record <check-id> pass|fail|skip "<detail>"` once per check
```

Rules for graders:

- Only the paths in `solution_paths` come from a submission. Everything else, including `grade/`, comes from `main`.
- Never run submitted code on the runner itself. Run it in a container (see `challenges/script-it/grade/run.sh`) or a throwaway cluster with checks before anything is applied (see `challenges/fix-the-deployment/grade/`).
- Pin every tool by version and checksum, and every image by digest.
- To parse a submission the way its tool does, a grader can use a small Go helper with the tool's own parser, in its own module in `grade/<helper>/` with a `go.sum`. `grade.sh` builds it with `go_build` from `scripts/grade-lib.sh` (see `challenges/write-the-alerts/grade/promcheck/`).
- Print output that came from a submission with `print_untrusted` from `scripts/grade-lib.sh`.
- Details go into a public PR comment. Say what is wrong, not how to fix it.
- Do not put a reference solution in the repo. Prove the starter fails and a solution passes in your PR description instead.

After the new challenge merges, a maintainer creates its `challenge:<id>` label.

## Fix a grader

If a grader passes something it should not, or fails a correct solution, open an issue with the steps to reproduce. For a way to fake a pass, use **Security > Report a vulnerability** instead.

## Local checks

```bash
python3 -m unittest discover -s tests   # tests that need PyYAML or pglast are skipped without them
for d in challenges/*/grade/*/go.mod; do (cd "$(dirname "$d")" && go vet ./... && go test ./...); done
shellcheck --norc --severity=style --external-sources scripts/*.sh challenges/*/grade/*.sh
actionlint
zizmor .github/workflows
```
