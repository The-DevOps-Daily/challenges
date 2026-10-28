#!/usr/bin/env bash
# Grader for fix-the-network-module: format and validate the module, run its
# terraform test file with a mock AWS provider, then plan it with random good
# and bad inputs and compare each plan with the network it should describe.
set -uo pipefail
# shellcheck source-path=SCRIPTDIR/../../..
# shellcheck source=scripts/grade-lib.sh
source "$GRADE_LIB"

TERRAFORM_IMAGE=hashicorp/terraform:1.16.6@sha256:bfffc1ded5fbf0a17beaaf22eb4793980abdb19d0761ff6a3a0efb2ab4d9b316
AWS_PROVIDER_VERSION=6.68.0
AWS_PROVIDER_SHA256=b2511842cb6b23036c72babbaf12b4984835bb2824f938882330b51c68bdaecd
later_checks=(module-tests inputs subnets tags)

out=$GRADE_OUT
module="$out/tf-module"
providers="$GRADE_TOOLS/tf-providers"
cases="$out/tf-cases"
# The expected results stay on the runner. No container ever sees them.
answers="$out/tf-answers.json"
uid_gid="$(id -u):$(id -g)"

skip() {
  local reason=$1
  shift
  for check in "$@"; do record "$check" skip "$reason"; done
}

# terraform runs in a container with no network, read-only system files and
# an unprivileged user. It sees a copy of the module (writable, for .terraform
# and plan files), the pinned AWS provider (read-only) and, for a plan, one
# case file. Nothing in the module can start a program: provisioners only run
# on apply, the test file from main only plans, and the only provider there is
# the pinned AWS one. Options before -- go to docker, the rest to terraform.
# Only terraform init may write to the module folder (it unpacks the
# provider into .terraform). Every later step sees it read-only, so nothing
# can change the provider or the files between steps; plans go to $plans.
tf() {
  local docker_args=() module_mode=ro tmpdir=/tmp
  if [[ $1 == --init ]]; then
    module_mode=rw
    tmpdir=/module/.tmp
    shift
  fi
  while [[ $1 != -- ]]; do
    docker_args+=("$1")
    shift
  done
  shift
  timeout 300 docker run --rm \
    --network none \
    --read-only \
    --tmpfs /tmp:rw,size=256m,mode=1777 \
    --user "$uid_gid" \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --memory 3g \
    --pids-limit 512 \
    --cpus 2 \
    -e HOME=/tmp \
    -e TMPDIR="$tmpdir" \
    -e TF_IN_AUTOMATION=1 \
    -e CHECKPOINT_DISABLE=1 \
    -v "$module:/module:$module_mode" \
    -v "$providers:/providers:ro" \
    "${docker_args[@]}" \
    -w /module \
    "$TERRAFORM_IMAGE" "$@"
}

# --- the module copy: main's versions.tf and tests, the PR's solution files ----------
plans="$out/tf-plans"
mkdir -p "$module/tests" "$module/.tmp" "$cases" "$plans" "$providers/registry.terraform.io/hashicorp/aws"
cp "$GRADE_WORK/versions.tf" "$module/" || exit 1
cp "$GRADE_WORK/tests/network.tftest.hcl" "$module/tests/" || exit 1
missing=()
for f in main.tf variables.tf outputs.tf; do
  if [[ -f $GRADE_WORK/$f ]]; then
    cp "$GRADE_WORK/$f" "$module/$f" || exit 1
  else
    missing+=("$f")
  fi
done
if ((${#missing[@]} > 0)); then
  record fmt-validate fail "The module is missing ${missing[*]}."
  skip "Skipped: the module is not complete." "${later_checks[@]}"
  exit 0
fi

# The hidden test below only works if the module cannot read files, and
# some blocks (provider, terraform_remote_state) could start a program
# through the AWS SDK's credential_process. See hclscan.py.
file_calls=$(timeout 60 python3 -I "$GRADE_DIR/hclscan.py" "$module/main.tf" "$module/variables.tf" "$module/outputs.tf")
scan_status=$?
if ((scan_status > 1)); then
  exit 1
elif ((scan_status == 1)); then
  found=$(head -n 2 <<<"$file_calls" | paste -sd ';' - | sed 's/;/; /g')
  record fmt-validate fail "$found. The module may only declare variables, locals, resources and outputs, and may not read files. If it is only words in a comment or string, reword them."
  skip "Skipped: the module has blocks or functions it may not use." "${later_checks[@]}"
  exit 0
fi

# --- pinned tools ---------------------------------------------------------------------
# -plugin-dir below makes terraform install providers only from this folder,
# which holds the AWS provider, checked against HashiCorp's SHA256SUMS.
zip="$providers/registry.terraform.io/hashicorp/aws/terraform-provider-aws_${AWS_PROVIDER_VERSION}_linux_amd64.zip"
if [[ ! -f $zip ]]; then
  fetch "https://releases.hashicorp.com/terraform-provider-aws/$AWS_PROVIDER_VERSION/terraform-provider-aws_${AWS_PROVIDER_VERSION}_linux_amd64.zip" \
    "$AWS_PROVIDER_SHA256" "$zip" || exit 1
fi
timeout 300 docker pull --quiet "$TERRAFORM_IMAGE" > /dev/null || exit 1

# --- fmt-validate -----------------------------------------------------------------------
tf -- fmt -check -no-color main.tf variables.tf outputs.tf > "$out/tf-fmt.log" 2>&1
fmt_status=$?
tf --init -- init -input=false -no-color -backend=false -plugin-dir=/providers > "$out/tf-init.log" 2>&1
init_status=$?
echo "::group::terraform init"
print_untrusted "$out/tf-init.log"
echo "::endgroup::"
if ((init_status != 0)); then
  first=$(grep -m1 -E '^Error: ' "$out/tf-init.log" | cut -c1-160)
  record fmt-validate fail "terraform init failed, with no network and only the AWS provider available. ${first:-See the log in the workflow run.}"
  skip "Skipped: terraform init failed." "${later_checks[@]}"
  exit 0
fi
tf -- validate -json -no-color > "$out/tf-validate.json" 2> "$out/tf-validate.err"
validate=$(python3 -I "$GRADE_DIR/check_plan.py" validate "$out/tf-validate.json") || exit 1
valid=$(python3 -I -c 'import json, sys; print(json.loads(sys.argv[1])["valid"])' "$validate")
echo "::group::terraform validate"
print_untrusted "$out/tf-validate.json"
echo "::endgroup::"
if [[ $valid != True ]]; then
  read -r count first < <(python3 -I -c 'import json, sys; r = json.loads(sys.argv[1]); print(r["count"], r["first"])' "$validate")
  record fmt-validate fail "terraform validate reports $count error(s). First: $first."
  skip "Skipped: terraform validate failed." "${later_checks[@]}"
  exit 0
fi
if ((fmt_status != 0)); then
  files=$(grep -E '^(main|variables|outputs)\.tf$' "$out/tf-fmt.log" | paste -sd, - | sed 's/,/, /g')
  record fmt-validate fail "terraform validate passes, but terraform fmt would change: ${files:-the files}."
else
  record fmt-validate pass "terraform fmt has nothing to change, and terraform validate passes."
fi

# --- module-tests -------------------------------------------------------------------------
tf -- test -no-color -json > "$out/tf-test.jsonl" 2> "$out/tf-test.err"
test_status=$?
echo "::group::terraform test"
print_untrusted "$out/tf-test.jsonl"
echo "::endgroup::"
tests=$(python3 -I "$GRADE_DIR/check_plan.py" tests "$out/tf-test.jsonl") || exit 1
read -r total failed_count failed_names < <(python3 -I -c '
import json, sys
r = json.loads(sys.argv[1])
print(r["total"], len(r["failed"]), ", ".join(r["failed"][:6]) or "-")
' "$tests")
if ((test_status == 0 && total > 0 && failed_count == 0)); then
  record module-tests pass "All $total runs in tests/network.tftest.hcl passed."
elif ((total == 0)); then
  record module-tests fail "terraform test did not run the tests (exit code $test_status)."
else
  record module-tests fail "$failed_count of $total runs in tests/network.tftest.hcl failed: $failed_names."
fi

# --- inputs, subnets, tags ----------------------------------------------------------------
# New random inputs for every run. Each plan gets one case file; the answers
# stay outside the container.
# A test with random mocked IDs, written now and known only to this run,
# checks that the outputs hold the right IDs and every subnet is in the VPC.
wiring="zz_wiring_$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n').tftest.hcl"
python3 -I "$GRADE_DIR/gen_cases.py" --cases "$cases" --answers "$answers" --wiring "$module/tests/$wiring" || exit 1
tf -- test -no-color -json -filter="tests/$wiring" > "$out/tf-wiring.jsonl" 2> "$out/tf-wiring.err"
echo "::group::terraform test with random IDs"
print_untrusted "$out/tf-wiring.jsonl"
echo "::endgroup::"
python3 -I "$GRADE_DIR/check_plan.py" tests "$out/tf-wiring.jsonl" > "$out/tf-wiring.json" || exit 1
rm -f "$module/tests/$wiring"

cp "$GRADE_DIR/grader_provider.tf" "$module/zz_grader_provider.tf" || exit 1
for case in "$cases"/*.tfvars.json; do
  id=$(basename "$case" .tfvars.json)
  [[ $id =~ ^[0-9a-f]{12}$ ]] || exit 1
  tf -v "$case:/case.tfvars.json:ro" -v "$plans:/plans" -- plan -input=false -lock=false -refresh=false -no-color -json \
    -var-file=/case.tfvars.json -out="/plans/$id.tfplan" > "$out/tf-$id.plan.jsonl" 2> "$out/tf-$id.plan.err"
  status=$?
  echo "$status" > "$out/tf-$id.rc"
  echo "::group::terraform plan, case $id (exit code $status)"
  print_untrusted "$out/tf-$id.plan.jsonl"
  echo "::endgroup::"
  if ((status == 0)); then
    tf -v "$plans:/plans:ro" -- show -json -no-color "/plans/$id.tfplan" > "$out/tf-$id.show.json" 2> /dev/null
  fi
done

score=$(python3 -I "$GRADE_DIR/check_plan.py" score "$out" "$answers") || exit 1
for check in inputs subnets tags; do
  IFS=$'\t' read -r status detail < <(python3 -I -c '
import json, sys
r = json.loads(sys.argv[1])[sys.argv[2]]
print(r["status"], r["detail"], sep="\t")
' "$score" "$check")
  record "$check" "$status" "$detail"
done
