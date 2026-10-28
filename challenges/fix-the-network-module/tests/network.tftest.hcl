# Tests for the network module. They use a mock AWS provider, so they need no
# AWS account and create nothing:
#
#   terraform init
#   terraform test
#
# The grader runs this file as one of its checks, then plans the module with
# random inputs of its own.

mock_provider "aws" {}

variables {
  name        = "shop"
  environment = "prod"
  vpc_cidr    = "10.20.0.0/16"
  azs         = ["eu-west-1b", "eu-west-1a", "eu-west-1c"]
  tags = {
    Team        = "payments"
    Environment = "wrong"
  }
}

run "vpc" {
  command = plan

  assert {
    condition     = aws_vpc.this.cidr_block == "10.20.0.0/16"
    error_message = "The VPC must use vpc_cidr."
  }

  assert {
    condition     = aws_vpc.this.enable_dns_support && aws_vpc.this.enable_dns_hostnames
    error_message = "The VPC must have DNS support and DNS hostnames turned on."
  }
}

run "one_public_and_one_private_subnet_per_az" {
  command = plan

  assert {
    condition     = length(aws_subnet.public) == 3 && length(aws_subnet.private) == 3
    error_message = "There must be one public and one private subnet per AZ."
  }

  assert {
    condition     = aws_subnet.public["eu-west-1a"].availability_zone == "eu-west-1a" && aws_subnet.private["eu-west-1c"].availability_zone == "eu-west-1c"
    error_message = "Subnets must be keyed by their AZ name and created in that AZ."
  }

  assert {
    condition     = aws_subnet.public["eu-west-1b"].map_public_ip_on_launch && !aws_subnet.private["eu-west-1b"].map_public_ip_on_launch
    error_message = "Only public subnets give instances a public IP."
  }
}

run "subnet_ranges_follow_the_az_letter" {
  command = plan

  assert {
    condition = {
      for az, subnet in aws_subnet.public : az => subnet.cidr_block
      } == {
      "eu-west-1a" = "10.20.0.0/20"
      "eu-west-1b" = "10.20.16.0/20"
      "eu-west-1c" = "10.20.32.0/20"
    }
    error_message = "Public subnet ranges do not match the AZ letters."
  }

  assert {
    condition = {
      for az, subnet in aws_subnet.private : az => subnet.cidr_block
      } == {
      "eu-west-1a" = "10.20.128.0/20"
      "eu-west-1b" = "10.20.144.0/20"
      "eu-west-1c" = "10.20.160.0/20"
    }
    error_message = "Private subnet ranges do not match the AZ letters."
  }
}

run "removing_an_az_leaves_the_others_alone" {
  command = plan

  variables {
    azs = ["eu-west-1a", "eu-west-1c"]
  }

  assert {
    condition     = aws_subnet.public["eu-west-1c"].cidr_block == "10.20.32.0/20" && aws_subnet.private["eu-west-1c"].cidr_block == "10.20.160.0/20"
    error_message = "The subnets of eu-west-1c moved when eu-west-1b was removed."
  }
}

run "small_vpc" {
  command = plan

  variables {
    vpc_cidr = "192.168.4.0/24"
    azs      = ["us-east-1f", "us-east-1d"]
  }

  assert {
    condition     = tomap(output.public_subnet_cidrs) == tomap({ "us-east-1d" = "192.168.4.48/28", "us-east-1f" = "192.168.4.80/28" })
    error_message = "public_subnet_cidrs is not the map of public subnet ranges by AZ."
  }

  assert {
    condition     = tomap(output.private_subnet_cidrs) == tomap({ "us-east-1d" = "192.168.4.176/28", "us-east-1f" = "192.168.4.208/28" })
    error_message = "private_subnet_cidrs is not the map of private subnet ranges by AZ."
  }

  assert {
    condition     = tolist(keys(output.public_subnet_ids)) == tolist(["us-east-1d", "us-east-1f"]) && tolist(keys(output.private_subnet_ids)) == tolist(["us-east-1d", "us-east-1f"])
    error_message = "The subnet ID outputs must be maps keyed by AZ."
  }
}

run "outputs_hold_the_ids" {
  command = plan

  # IDs only exist after apply. These overrides give the VPC and the
  # subnets known IDs during plan, so the outputs and the subnets' vpc_id
  # can be compared with them.
  override_resource {
    target          = aws_vpc.this
    override_during = plan
    values = {
      id = "vpc-0123456789abcdef0"
    }
  }

  override_resource {
    target          = aws_subnet.public["eu-west-1a"]
    override_during = plan
    values = {
      id = "subnet-0aaaaaaaaaaaaaaa1"
    }
  }

  override_resource {
    target          = aws_subnet.public["eu-west-1b"]
    override_during = plan
    values = {
      id = "subnet-0aaaaaaaaaaaaaaa2"
    }
  }

  override_resource {
    target          = aws_subnet.public["eu-west-1c"]
    override_during = plan
    values = {
      id = "subnet-0aaaaaaaaaaaaaaa3"
    }
  }

  override_resource {
    target          = aws_subnet.private["eu-west-1a"]
    override_during = plan
    values = {
      id = "subnet-0bbbbbbbbbbbbbbb1"
    }
  }

  override_resource {
    target          = aws_subnet.private["eu-west-1b"]
    override_during = plan
    values = {
      id = "subnet-0bbbbbbbbbbbbbbb2"
    }
  }

  override_resource {
    target          = aws_subnet.private["eu-west-1c"]
    override_during = plan
    values = {
      id = "subnet-0bbbbbbbbbbbbbbb3"
    }
  }

  assert {
    condition     = output.vpc_id == "vpc-0123456789abcdef0"
    error_message = "vpc_id is not the ID of aws_vpc.this."
  }

  assert {
    condition     = alltrue([for subnet in concat(values(aws_subnet.public), values(aws_subnet.private)) : subnet.vpc_id == "vpc-0123456789abcdef0"])
    error_message = "Every subnet must be in aws_vpc.this."
  }

  assert {
    condition = tomap(output.public_subnet_ids) == tomap({
      "eu-west-1a" = "subnet-0aaaaaaaaaaaaaaa1"
      "eu-west-1b" = "subnet-0aaaaaaaaaaaaaaa2"
      "eu-west-1c" = "subnet-0aaaaaaaaaaaaaaa3"
    })
    error_message = "public_subnet_ids does not map each AZ to the ID of its public subnet."
  }

  assert {
    condition = tomap(output.private_subnet_ids) == tomap({
      "eu-west-1a" = "subnet-0bbbbbbbbbbbbbbb1"
      "eu-west-1b" = "subnet-0bbbbbbbbbbbbbbb2"
      "eu-west-1c" = "subnet-0bbbbbbbbbbbbbbb3"
    })
    error_message = "private_subnet_ids does not map each AZ to the ID of its private subnet."
  }
}

run "tags" {
  command = plan

  assert {
    condition = aws_vpc.this.tags == tomap({
      Name        = "shop-prod"
      Environment = "prod"
      ManagedBy   = "terraform"
      Team        = "payments"
    })
    error_message = "The VPC tags are wrong. The module's own tags must win over the caller's."
  }

  assert {
    condition = aws_subnet.private["eu-west-1c"].tags == tomap({
      Name        = "shop-prod-private-eu-west-1c"
      Environment = "prod"
      ManagedBy   = "terraform"
      Team        = "payments"
      Tier        = "private"
    })
    error_message = "The private subnet tags are wrong."
  }

  assert {
    condition     = aws_subnet.public["eu-west-1a"].tags["Name"] == "shop-prod-public-eu-west-1a" && aws_subnet.public["eu-west-1a"].tags["Tier"] == "public"
    error_message = "The public subnet tags are wrong."
  }
}

run "rejects_a_public_cidr" {
  command = plan

  variables {
    vpc_cidr = "8.8.0.0/16"
  }

  expect_failures = [var.vpc_cidr]
}

run "rejects_a_vpc_larger_than_16" {
  command = plan

  variables {
    vpc_cidr = "10.0.0.0/8"
  }

  expect_failures = [var.vpc_cidr]
}

run "rejects_a_bad_name" {
  command = plan

  variables {
    name = "Shop_API"
  }

  expect_failures = [var.name]
}

run "rejects_an_unknown_environment" {
  command = plan

  variables {
    environment = "production"
  }

  expect_failures = [var.environment]
}

run "rejects_a_single_az" {
  command = plan

  variables {
    azs = ["eu-west-1a"]
  }

  expect_failures = [var.azs]
}

run "rejects_azs_in_two_regions" {
  command = plan

  variables {
    azs = ["eu-west-1a", "eu-central-1b"]
  }

  expect_failures = [var.azs]
}
