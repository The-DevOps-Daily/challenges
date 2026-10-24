# Fix the deployment

Kubernetes · Intermediate · about 45 minutes

## The situation

The inventory API was moved to Kubernetes on Friday. On Monday it is still not serving: the rollout never finishes, and the few times a pod looked healthy, nothing answered behind the Service. The person who wrote the manifests is on holiday.

The app itself is fine. It is a small Go service in `app/`:

- It reads the store name from the `STORE_NAME` environment variable and refuses to start without it.
- It listens on port 8080 (or `PORT`).
- `GET /healthz` answers `ok`.
- `GET /` answers JSON with the store name and its stock.

The manifests in `k8s/` are where the bugs are. There is more than one.

## Your task

Fix the files in `k8s/` so that:

- the `inventory-api` Deployment rolls out with 2 of 2 replicas ready,
- the `inventory-api` Service routes to the pods,
- `GET /` on port 80 of the Service answers HTTP 200 for the store named in the ConfigMap (`riverside`),
- the pods stay up without restarts.

Keep these as they are:

- the image `devops-daily/inventory-api:1.0`, one container per pod and 2 replicas,
- a readiness probe and a liveness probe, both HTTP (`httpGet`) probes with delays and periods of at most 60 seconds (fix them, do not delete them),
- `STORE_NAME` set from a ConfigMap, and the store name `riverside`,
- the Service on port 80, type `ClusterIP`.

Do not set a namespace in the manifests; the grader applies them to its own namespace.

## What the grader checks

The grader builds the app image from `main`, starts a [kind](https://kind.sigs.k8s.io/) cluster (Kubernetes 1.37), loads the image, and applies your `k8s/` files to a namespace that enforces Pod Security "baseline".

| Check | Passes when |
| --- | --- |
| The manifests apply | The files in `k8s/` parse and apply, and define only the `inventory-api` Deployment and Service plus ConfigMaps. The image, the 2 replicas and both HTTP probes stay in place, `STORE_NAME` comes from a ConfigMap, and pods stay off the node's namespaces and files. |
| The rollout finishes | `kubectl rollout status deployment/inventory-api` succeeds within 120 seconds, with 2 of 2 replicas ready. |
| The Service has endpoints | The `inventory-api` Service routes to at least one ready pod. |
| The Service answers | `GET /` on port 80 of the Service's cluster IP answers HTTP 200 with the store name from the ConfigMap. |
| The pods stay up | 30 seconds after the other checks, the grader calls the URLs both probes call and they answer, 2 of 2 replicas are still ready, and no container restarted. |

When a check fails, the grader says what it saw (for example, how many pods are ready and in which state), not what to change.

## Files you may change

- `challenges/fix-the-deployment/k8s/*.yaml` (or `*.yml`): edit the three files, or split and add files in `k8s/`.

A PR that changes any other file is not graded.

## Hints

<details>
<summary>Hint 1: start where Kubernetes tells you</summary>

`kubectl get pods`, then `kubectl describe pod <name>` and read the Events at the bottom. `kubectl logs <pod>` shows what the app printed before it stopped.
</details>

<details>
<summary>Hint 2: a pod that runs but is never ready</summary>

Something decides when a pod is ready. Ask the app what it actually serves: `kubectl port-forward deploy/inventory-api 8080:8080`, then `curl` the paths the probes use.
</details>

<details>
<summary>Hint 3: ready pods, but the Service does not answer</summary>

`kubectl get endpointslices -l kubernetes.io/service-name=inventory-api` shows which pods the Service sends traffic to, and on which port. Compare the Service with the pods it should select.
</details>

## Test it locally

You need Docker, [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) and kubectl. From this folder:

```bash
kind create cluster --name inventory
docker build -t devops-daily/inventory-api:1.0 app/
kind load docker-image devops-daily/inventory-api:1.0 --name inventory

kubectl create namespace challenge
kubectl label namespace challenge pod-security.kubernetes.io/enforce=baseline
kubectl apply -n challenge -f k8s/
kubectl -n challenge rollout status deployment/inventory-api --timeout=120s

kubectl -n challenge run curl --rm -it --restart=Never --image=curlimages/curl -- curl -s http://inventory-api/

kind delete cluster --name inventory
```

To start over after a change: `kubectl delete namespace challenge`, then create and label it again, or reapply and wait for the rollout.

## Submit

1. Fork this repo and create a branch.
2. Commit your changes in `challenges/fix-the-deployment/k8s/`.
3. Open a PR against `main` in `The-DevOps-Daily/challenges`.

The grader comments on the PR with a result for each check. Push more commits to the same branch until every check passes. Then the PR gets the `passed` label and is closed. It is never merged. Grading takes about 4 minutes, most of it starting the cluster.
