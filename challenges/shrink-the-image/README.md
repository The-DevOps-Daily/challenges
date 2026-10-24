# Shrink the image

Docker · Starter · about 30 minutes

## The situation

The slug service is tiny: one Flask file (`app.py`) and three dependencies. It turns any text into a URL-friendly slug:

```console
$ curl 'localhost:8000/slugify?text=Hello World'
{"slug":"hello-world","text":"Hello World"}
```

Its container image is over 1 GB. Every deploy pulls it again, the registry bill keeps growing, and the security scanner lists hundreds of packages the app never uses. It also runs as root. Nobody has touched the `Dockerfile` since the first prototype.

## Your task

Rewrite `Dockerfile`, and add a `.dockerignore` if it helps, so that the image:

- builds,
- is at most 200 MB,
- runs as a user that is not root,
- starts and answers `GET /health` on port 8000 within 20 seconds,
- still runs the real app.

Leave `app.py`, `requirements.txt`, `requirements-dev.txt` and `tests/` as they are. The grader uses the copies from `main` in any case.

### Why 200 MB

`python:3.13-slim` plus the three runtime dependencies comes out at about 150 MB. The full `python:3.13` base image alone is over 1 GB, and compilers and dev tools add hundreds of MB on top. A limit of 200 MB leaves room for any reasonable slim image, and rules out the full base image and images that keep build tools or dev dependencies. Alpine and distroless images can get much smaller, but you do not need them to pass.

## What the grader checks

| Check | Passes when |
| --- | --- |
| The image builds | `docker build` finishes within 10 minutes on a GitHub-hosted `ubuntu-24.04` runner. A `# syntax=` line may only name `docker/dockerfile`. |
| Under 200 MB | The image is at most 200 MB (200,000,000 bytes), as `docker image inspect` reports it. |
| Runs as a non-root user | The image sets a `USER` that is not root, and no process in the running container has UID 0. |
| Starts and answers /health | The container, started with no extra arguments, answers `GET /health` on port 8000 with HTTP 200 within 20 seconds. |
| The app still works | `GET /slugify?text=...` returns the right slug for a random text. |

## Files you may change

- `challenges/shrink-the-image/Dockerfile`
- `challenges/shrink-the-image/.dockerignore`

A PR that changes any other file is not graded.

## Hints

<details>
<summary>Hint 1: where do the gigabytes come from?</summary>

Run `docker history <image>` on the current image. Compare the size of the first layers with the layers your own instructions add.
</details>

<details>
<summary>Hint 2: what does the app need at run time?</summary>

The app needs Python and the packages in `requirements.txt`. It does not need a compiler, git, vim, the dev requirements, the tests or pip's download cache.
</details>

<details>
<summary>Hint 3: who runs the process?</summary>

A container runs as root unless the image says otherwise. Create a user in the image and switch to it with `USER` before `CMD`.
</details>

## Test it locally

From this folder, with Docker installed:

```bash
docker build -t shrink-the-image .
docker image ls shrink-the-image                      # SIZE is what the size check reads
docker image inspect -f '{{.Config.User}}' shrink-the-image
docker run --rm -d --name shrink -p 8000:8000 shrink-the-image
curl -i localhost:8000/health
curl 'localhost:8000/slugify?text=Shrink This Image'
docker top shrink -eo uid,cmd                         # no line should show UID 0
docker rm -f shrink
```

## Submit

1. Fork this repo and create a branch.
2. Commit your `Dockerfile` (and `.dockerignore`) in `challenges/shrink-the-image/`.
3. Open a PR against `main` in `The-DevOps-Daily/challenges`.

The grader comments on the PR with a result for each check. Push more commits to the same branch until every check passes. Then the PR gets the `passed` label and is closed. It is never merged.
