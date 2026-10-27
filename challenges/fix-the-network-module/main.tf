locals {
  prefix = "${var.name}-${var.environment}"

  tags = merge({
    Name        = local.prefix
    Environment = var.environment
    ManagedBy   = "terraform"
  }, var.tags)
}

resource "aws_vpc" "this" {
  cidr_block          = var.vpc_cidr
  enable_dns_support  = true
  enable_dns_hostname = true

  tags = local.tags
}

resource "aws_subnet" "public" {
  count = length(var.azs)

  vpc_id                  = aws_vpc.main.id
  availability_zone       = var.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  map_public_ip_on_launch = true

  tags = merge(local.tags, {
    Name = "${local.prefix}-public-${var.azs[count.index]}"
    Tier = "public"
  })
}

resource "aws_subnet" "private" {
  count = length(var.azs)

  vpc_id            = aws_vpc.main.id
  availability_zone = var.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 1)

  tags = merge(local.tags, {
    Name = "${local.prefix}-private-${var.azs[count.index]}"
    Tier = "private"
  })
}
