package main

import (
	"os"
	"strings"
	"testing"
)

// The resources most snippets refer to, as a second file of the module.
const declarations = `resource "aws_vpc" "this" {}
resource "aws_subnet" "public" { for_each = var.azs }
resource "aws_subnet" "private" { for_each = var.azs }
resource "aws_subnet" "counted" { count = 2 }
`

func problemsIn(src string) []string {
	return check(map[string][]byte{"main.tf": []byte(src), "decl.tf": []byte(declarations)})
}

func TestStarterFilesPass(t *testing.T) {
	// The starter is broken, but in ways terraform validate reports. The
	// learner should see those errors first, not this check's.
	files := map[string][]byte{}
	for _, name := range []string{"main.tf", "variables.tf", "outputs.tf"} {
		src, err := os.ReadFile("../../" + name)
		if err != nil {
			t.Fatal(err)
		}
		files[name] = src
	}
	if got := check(files); len(got) != 0 {
		t.Errorf("%v", got)
	}
}

func TestAllowedForms(t *testing.T) {
	for _, src := range []string{
		`output "vpc_id" { value = aws_vpc.this.id }`,
		`output "a" { value = { for az, subnet in aws_subnet.public : az => subnet.id } }`,
		`output "a" { value = tomap({ for az, subnet in aws_subnet.public : az => subnet.cidr_block }) }`,
		`output "a" { value = { for az in var.azs : az => aws_subnet.public[az].id } }`,
		`output "a" { value = { for s in aws_subnet.private : s.availability_zone => s.id } }`,
		`output "a" { value = zipmap(keys(aws_subnet.public), [for s in values(aws_subnet.public) : s.id]) }`,
		`output "a" { value = aws_subnet.public[*].id }`,
		`output "a" { value = aws_subnet.public.*.cidr_block }`,
		`output "a" { value = values(aws_subnet.public)[*].id }`,
		`output "a" { value = aws_subnet.public["eu-west-1a"].vpc_id }`,
		`output "a" { value = length(aws_subnet.public) }`,
		`output "a" { value = { for az, s in aws_subnet.public : az => s["id"] } }`,
		`output "a" { value = aws_subnet.public["eu-west-1a"]["cidr_block"] }`,
		`output "a" { value = aws_subnet.public[each.key]["id"] }`,
		`output "a" { value = aws_vpc.this["id"] }`,
		`output "a" { value = aws_subnet.counted[0].id }`,
		`output "a" { value = aws_subnet.counted[*]["vpc_id"] }`,
		`output "a" {
  value      = aws_vpc.this.id
  depends_on = [aws_subnet.public]
}`,
		`locals {
  tags   = merge(var.tags, { Name = "${var.name}-${var.environment}", ManagedBy = "terraform" })
  blocks = { for az in var.azs : az => index(["a", "b", "c", "d", "e", "f"], substr(az, length(az) - 1, 1)) }
  aws_vpc = "a literal key"
}`,
		`resource "aws_subnet" "public" {
  for_each   = local.blocks
  vpc_id     = aws_vpc.this.id
  cidr_block = cidrsubnet(var.vpc_cidr, 4, each.value)
  tags       = merge(local.tags, { Name = "x-${each.key}" })

  lifecycle {
    ignore_changes        = [tags]
    replace_triggered_by  = [aws_vpc.this]
  }
}`,
		`variable "azs" {
  type = list(string)
  validation {
    condition     = alltrue([for az in var.azs : can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]+[a-f]$", az))])
    error_message = "Each entry must look like eu-west-1a; file() is not needed."
  }
}`,
		"# file(\"x\") in a comment, and data \"terraform_remote_state\" \"x\" {}\noutput \"a\" { value = \"file(/etc/hostname)\" }",
	} {
		if got := problemsIn(src); len(got) != 0 {
			t.Errorf("%s\n  got %v", src, got)
		}
	}
}

func TestBlocksAndResourceTypes(t *testing.T) {
	for src, want := range map[string]string{
		`data "terraform_remote_state" "x" {}`:               "has a data block",
		"data \"terraform_remote_state\" x́ {\n}":            "has a data block",
		"data /* c */ \"aws_x\" /* c */ \"y\" {}":            "has a data block",
		`provider "aws" { region = "eu-west-1" }`:            "has a provider block",
		"terraform {\n  backend \"s3\" {}\n}":                "has a terraform block",
		`module "m" { source = "./x" }`:                      "has a module block",
		`check "c" {}`:                                       "has a check block",
		"import {\n  to = aws_vpc.this\n  id = \"vpc-1\"\n}": "has a import block",
		"moved {\n  from = aws_vpc.a\n  to = aws_vpc.b\n}":   "has a moved block",
		`ephemeral "aws_x" "y" {}`:                           "has a ephemeral block",
		`resource "aws_internet_gateway" "gw" {}`:            "has a resource of type aws_internet_gateway",
		`resource "terraform_data" "x" { input = 1 }`:        "has a resource of type terraform_data",
		`x = 1`:               "has an argument outside a block",
		"locals {\n  a = 1\n": "does not parse",
	} {
		got := problemsIn(src)
		if len(got) == 0 || !strings.Contains(got[0], want) {
			t.Errorf("%q: want %q, got %v", src, want, got)
		}
	}
}

func TestFunctionsThatReadFilesOrPaths(t *testing.T) {
	for src, want := range map[string]string{
		`locals { a = file("/etc/hostname") }`:                      "calls file()",
		`locals { a = core::file("x") }`:                            "calls file()",
		`locals { a = "${fileset("/", "*")}" }`:                     "calls fileset()",
		"locals {\n  a = <<EOT\n${templatefile(\"t\", {})}\nEOT\n}": "calls templatefile()",
		`locals { a = templatestring(local.t, {}) }`:                "calls templatestring()",
		`locals { a = abspath(".") }`:                               "calls abspath()",
		`locals { a = pathexpand("~") }`:                            "calls pathexpand()",
		`locals { a = 1-file("x") }`:                                "calls file()",
		`locals { a = [for f in [1] : filemd5("x")] }`:              "calls filemd5()",
		`locals { a = provider::aws::arn_parse(var.name) }`:         "calls the provider function provider::aws::arn_parse()",
		`locals { a = provider::terraform::encode_expr(var.tags) }`: "calls the provider function",
	} {
		got := problemsIn(src)
		if len(got) == 0 || !strings.Contains(got[0], want) {
			t.Errorf("%q: want %q, got %v", src, want, got)
		}
	}
}

func TestReferencesThatCouldTellTheGraderApart(t *testing.T) {
	for _, src := range []string{
		// Computed attributes that a mock provider makes up.
		`output "vpc_id" { value = startswith(aws_vpc.this.arn, "arn:") ? "wrong" : aws_vpc.this.id }`,
		`output "vpc_id" { value = aws_vpc.this.instance_tenancy == null ? aws_vpc.this.id : "wrong" }`,
		`output "a" { value = { for az, s in aws_subnet.public : az => length(s.owner_id) == 12 ? "x" : s.id } }`,
		`output "a" { value = [for s in values(aws_subnet.public) : s.arn] }`,
		`output "a" { value = aws_subnet.public["a"].map_public_ip_on_launch }`,
		`output "a" { value = aws_subnet.public[*].arn }`,
		`output "a" { value = aws_subnet.public.*.tags_all }`,
		`output "a" { value = aws_subnet.public[each.key].owner_id }`,
		`output "a" { value = aws_vpc.this.tags["Name"] }`,
		`output "a" { value = aws_vpc.this["arn"] }`,
		`output "a" { value = { for az, s in aws_subnet.public : az => s["arn"] } }`,
		`output "a" { value = aws_vpc.this[local.attribute] }`,
		`output "a" { value = aws_vpc.this[local.attribute].id }`,
		`output "a" { value = aws_subnet.public["eu-west-1a"]["tags"] }`,
		`output "a" { value = aws_subnet.public["eu-west-1a"] }`,
		// A whole resource as a value.
		`output "a" { value = jsonencode(aws_vpc.this) }`,
		`output "a" { value = lookup(aws_vpc.this, "arn", "") }`,
		`output "a" { value = { for k, s in aws_subnet.public : k => s } }`,
		`output "a" { value = values(aws_subnet.public)[0].id }`,
		`output "a" { value = aws_subnet.public[*] }`,
		`output "a" { value = merge(aws_subnet.public, {}) }`,
		`locals { all = aws_subnet.public }`,
		`resource "aws_subnet" "b" { for_each = aws_subnet.public }`,
		`output "a" { value = (aws_subnet.public)["x"].id }`,
		`output "a" { value = [for x in [aws_vpc.this] : x.id] }`,
		`output "a" { value = "${aws_vpc.this.arn}" }`,
		"output \"a\" {\n  value = <<EOT\n%{for s in aws_subnet.public}${s.arn}%{endfor}\nEOT\n}",
		// The grader's machine and the plan or apply phase.
		`output "a" { value = path.module == "." ? aws_vpc.this.id : "wrong" }`,
		`output "a" { value = path.cwd }`,
		`output "a" { value = terraform.applying ? "wrong" : aws_vpc.this.id }`,
		`output "a" { value = terraform.workspace }`,
		`output "a" { value = resource.aws_vpc.this.arn }`,
		"resource \"aws_vpc\" \"this\" {\n  lifecycle {\n    postcondition {\n      condition     = self.arn != \"\"\n      error_message = \"x\"\n    }\n  }\n}",
		`output "a" { value = data.aws_caller_identity.x.account_id }`,
		`resource "aws_vpc" "this" {
  dynamic "x" {
    for_each = [1]
    content { y = x.value }
  }
}`,
	} {
		if got := problemsIn(src); len(got) == 0 {
			t.Errorf("%s: no problem found", src)
		}
	}
}

func TestLinesAndFileNames(t *testing.T) {
	got := check(map[string][]byte{"/module/outputs.tf": []byte("output \"a\" {\n  value = aws_subnet.public[\"b\"].arn\n}\n"),
		"/module/main.tf": []byte(declarations)})
	if len(got) != 1 || !strings.HasPrefix(got[0], "outputs.tf line 2 reads aws_subnet.public.b.arn") {
		t.Errorf("got %v", got)
	}
}
