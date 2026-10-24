# DevOps Daily challenges

Fix a real thing, open a pull request, and CI grades it.

Each challenge is a folder with something broken or unfinished in it: a bloated Dockerfile, a half-written script, a Deployment that never comes up. You fix it in your fork and open a PR against this repo. A GitHub Actions workflow grades the PR, comments pass or fail for each check, and closes the PR when you pass. Submissions are never merged.

All challenges, with how many people passed each one: [devops-daily.com/challenges](https://devops-daily.com/challenges)

## Challenges

| Challenge | Topic | Level | Time |
| --- | --- | --- | --- |
| [Shrink the image](challenges/shrink-the-image) | Docker | Starter | 30 min |
| [Script it: summarize an access log](challenges/script-it) | Bash | Starter | 45 min |
| [Fix the deployment](challenges/fix-the-deployment) | Kubernetes | Intermediate | 45 min |

## How it works

1. Fork this repo.
2. Pick a challenge and read its `README.md`. It describes the situation, what the grader checks, and which files you may change.
3. Fix it on a branch in your fork. Each README says how to test your fix locally.
4. Open a PR from that branch to `main` in this repo. Grading takes 1 to 5 minutes.
5. Read the grader's comment. If a check fails, push another commit to the same branch and it grades again. When every check passes, the PR gets the `passed` label and is closed.

## Rules

- One challenge per PR.
- Change only the challenge's solution files. Each README lists them. A PR that changes anything else, including the tests, the grader or the workflows, is not graded.
- Solution files must be regular files of at most 64 KB. No symlinks or submodules.
- Your PR and your solution are public. Do not put secrets in them.
- GitHub does not run the grader on a PR with merge conflicts. If your PR shows one, update your branch from `main` and push again.

## How grading stays honest

You control your PR, so the grader never trusts it:

- The tests, the grader and the workflow come from `main`. The Grade workflow copies only the challenge's solution files out of your PR, through the GitHub API, into a fresh copy of the challenge from `main`. Your branch is never checked out.
- Your code runs only in containers or a throwaway kind cluster. A script runs in a container with no network, an unprivileged user and read-only files. A Dockerfile builds with a pinned official frontend. Kubernetes manifests are checked before anything is applied, and the namespace enforces Pod Security "restricted".
- The Grade workflow has a read-only token and no secrets. It only uploads a small result file. The Report workflow runs from `main`, reads that file as data, checks it against the files your PR's head commit changes, and only then comments, labels and closes.
- Some inputs are random at grading time (generated logs, random text for the app), so the checks do not compare against fixed answers you could copy.

Found a way around it? Report it privately under **Security > Report a vulnerability** instead of opening a PR.

## Pass counts

The Stats workflow counts passes per challenge, once per GitHub user, and writes them to the [`stats` branch](https://github.com/The-DevOps-Daily/challenges/tree/stats): `stats.json` has the counts that devops-daily.com shows, and `passes.json` lists who passed what.

## Propose a challenge

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
