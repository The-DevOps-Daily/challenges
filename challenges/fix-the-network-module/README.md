# Fix the network module

Terraform · Intermediate · about 60 minutes

## The situation

Every team builds its VPC with the platform team's `network` module. Last sprint someone refactored it, and it has not worked since:

- `terraform validate` fails, so nobody can use the new version.
- Before the refactor, the private subnet of one AZ overlapped the public subnet of the next AZ. AWS refused to create it in two accounts before anyone noticed. The refactor did not fix it.
- Subnets are created with `count`. When a team removed `eu-west-1a` from a test stack, Terraform wanted to destroy and recreate every subnet.
- Nothing checks the inputs. Last month someone created a VPC with a public `/8` range.
- A team passed its own `Name` tag, and every resource in its VPC ended up with that one name.

Fix the module.

## What the module must do

### Inputs (`variables.tf`)

| Variable | Type | Rules |
| --- | --- | --- |
| `name` | `string` | 3 to 20 characters: lowercase letters, digits and hyphens. Starts with a letter and ends with a letter or digit. |
| `environment` | `string` | `dev`, `staging` or `prod`. |
| `vpc_cidr` | `string` | An IPv4 CIDR block such as `10.20.0.0/16`, with a prefix length from /16 to /24, inside `10.0.0.0/8`, `172.16.0.0/12` or `192.168.0.0/16`. |
| `azs` | `list(string)` | 2 to 4 availability zones, all different and all in the same region. Each is a name such as `eu-west-1a` that ends in a letter from `a` to `f`. |
| `tags` | `map(string)` | Extra tags for every resource. Optional, `{}` by default. |

Write each rule as a `validation` block, so a bad value stops `terraform plan` with an "Invalid value for variable" error.

### Resources (`main.tf`)

- `aws_vpc.this`, with `cidr_block` set to `vpc_cidr`, and DNS support and DNS hostnames turned on.
- `aws_subnet.public` and `aws_subnet.private`: one of each per AZ, keyed by the AZ name (`aws_subnet.public["eu-west-1a"]`) and created in that AZ. Public subnets set `map_public_ip_on_launch` to `true`, private subnets to `false`.
- Subnet ranges: split the VPC into 16 equal blocks, numbered from 0. The AZ's letter picks the block of its public subnet: `a` is block 0, `b` is block 1, and so on up to `f`, block 5. The private subnet of the same AZ is 8 blocks higher. In `10.20.0.0/16`, `eu-west-1b` always gets `10.20.16.0/20` (public) and `10.20.144.0/20` (private), wherever it is in the list.
- Tags: every resource gets the caller's `tags` plus the tags below. When the caller passes a tag with the same key, the module's value wins.
  - `Name`: `<name>-<environment>` on the VPC, and `<name>-<environment>-public-<az>` or `<name>-<environment>-private-<az>` on a subnet,
  - `Environment`: the environment,
  - `ManagedBy`: `terraform`,
  - `Tier`: `public` or `private`, on subnets only.

### Outputs (`outputs.tf`)

- `vpc_id`: the ID of the VPC.
- `public_subnet_ids` and `private_subnet_ids`: maps from AZ name to subnet ID.
- `public_subnet_cidrs` and `private_subnet_cidrs`: maps from AZ name to the subnet's CIDR block.

Leave `versions.tf` and `tests/` as they are; the grader uses the copies from `main`. The module must not configure the AWS provider itself. The caller does that, and the grader does it for its own plans. The module must not read files either: `file()`, `templatefile()` and the other file functions are not allowed, and the grader also counts words in a comment or string that look like a call to one of them.

## What the grader checks

The grader runs Terraform 1.16 with the AWS provider 6.68 in a container with no network and no AWS account. Nothing is ever created: `terraform plan` only shows what would be.

| Check | Passes when |
| --- | --- |
| Formatted and valid | The module calls no file function, `terraform init` works with no network and only the AWS provider, `terraform validate` passes, and `terraform fmt` has nothing to change in `main.tf`, `variables.tf` and `outputs.tf`. |
| The module's tests pass | `terraform test` passes every run in `tests/network.tftest.hcl`. The tests use a mock AWS provider, and one of them gives the VPC and subnets known IDs to check the outputs and that every subnet is in the VPC. |
| Inputs are validated | For every rule in the inputs table, a random value that breaks it (20 in all) is rejected by a validation rule, and three random sets of good inputs plan without errors. |
| Subnets for random inputs | For three random sets of good inputs, the plan has `aws_vpc.this` with DNS turned on, and one public and one private subnet per AZ in that VPC, keyed by AZ name, with the range the AZ letter picks. The outputs map AZs to subnet ranges and IDs: a test written for each run gives the VPC and the subnets random IDs and checks where they end up. |
| Tags | In the same plans, the VPC and every subnet have the caller's tags plus `Name`, `Environment`, `ManagedBy` and, on subnets, `Tier`, and the module's values win. |

The random inputs are new for every run: names, environments, VPC ranges from all three private ranges, regions, AZ letters in any order, and caller tags (sometimes with a `Name` or `Environment` of their own, and sometimes no `tags` at all). When a check fails, the grader says which input it used and what it saw.

## Files you may change

- `challenges/fix-the-network-module/main.tf`
- `challenges/fix-the-network-module/variables.tf`
- `challenges/fix-the-network-module/outputs.tf`

A PR that changes any other file is not graded. The folder's `.gitignore` keeps `.terraform/` and the lock file that `terraform init` creates out of your commits.

## Hints

<details>
<summary>Hint 1: start with what Terraform tells you</summary>

Run `terraform validate` and read each error. It names the file and the line.
</details>

<details>
<summary>Hint 2: count or for_each</summary>

Resources created with `count` are known by their position in the list, so removing the first AZ shifts every other subnet. Resources created with `for_each` are known by their key.
</details>

<details>
<summary>Hint 3: from AZ letter to block number</summary>

Look at `substr()`, `index()` and `cidrsubnet()` in the [Terraform function docs](https://developer.hashicorp.com/terraform/language/functions).
</details>

<details>
<summary>Hint 4: validation rules that do not crash</summary>

A function such as `cidrhost()` fails on a value that is not a CIDR block. `can()` turns that failure into `false`. A variable can have more than one `validation` block, each with its own error message.
</details>

<details>
<summary>Hint 5: which tag wins</summary>

`merge()` keeps the value from the last map that has the key.
</details>

## Test it locally

You need Terraform 1.11 or later. You do not need an AWS account: the tests use a mock provider. From this folder:

```bash
terraform init
terraform validate
terraform fmt -check
terraform test
```

To try random inputs like the grader's, plan with each case file and compare with the answers file, which says for each case whether it is good or bad and why:

```bash
python3 grade/gen_cases.py --cases /tmp/net-cases --answers /tmp/net-answers.json
cp grade/grader_provider.tf zz_grader_provider.tf    # fake keys, so plan works offline
for f in /tmp/net-cases/*.tfvars.json; do
  if terraform plan -refresh=false -var-file="$f" > /dev/null 2>&1; then echo "planned:  $f"; else echo "rejected: $f"; fi
done
rm zz_grader_provider.tf
```

## Submit

1. Fork this repo and create a branch.
2. Commit your `main.tf`, `variables.tf` and `outputs.tf` in `challenges/fix-the-network-module/`.
3. Open a PR against `main` in `The-DevOps-Daily/challenges`.

The grader comments on the PR with a result for each check. Push more commits to the same branch until every check passes. Then the PR gets the `passed` label and is closed. It is never merged. If GitHub shows a merge conflict on the PR, the grader cannot run: update your branch from `main` first. Grading takes about 3 minutes.
