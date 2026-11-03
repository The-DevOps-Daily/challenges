locals {
  prefix = "${var.name}-${var.environment}"

  module_tags = {
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  # The AZ letter picks the block: a = 0 ... f = 5. Private subnets are 8 blocks higher.
  az_block = { for az in var.azs : az => index(["a", "b", "c", "d", "e", "f"], substr(az, -1, 1)) }
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, local.module_tags, { Name = local.prefix })
}

resource "aws_subnet" "public" {
  for_each = local.az_block

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, each.value)
  map_public_ip_on_launch = true

  tags = merge(var.tags, local.module_tags, {
    Name = "${local.prefix}-public-${each.key}"
    Tier = "public"
  })
}

resource "aws_subnet" "private" {
  for_each = local.az_block

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, each.value + 8)
  map_public_ip_on_launch = false

  tags = merge(var.tags, local.module_tags, {
    Name = "${local.prefix}-private-${each.key}"
    Tier = "private"
  })
}
