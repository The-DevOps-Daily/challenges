variable "name" {
  description = "Name of the network. Used in the Name tag of every resource."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name))
    error_message = "name must be 3 to 20 lowercase letters, digits and hyphens, start with a letter and end with a letter or digit."
  }
}

variable "environment" {
  description = "Environment the network is for: dev, staging or prod."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "vpc_cidr" {
  description = "IPv4 CIDR block of the VPC."
  type        = string

  validation {
    condition     = can(regex("^\\d+\\.\\d+\\.\\d+\\.\\d+/\\d+$", var.vpc_cidr)) && can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be an IPv4 CIDR block such as 10.20.0.0/16."
  }

  validation {
    condition     = try(tonumber(split("/", var.vpc_cidr)[1]) >= 16 && tonumber(split("/", var.vpc_cidr)[1]) <= 24, false)
    error_message = "vpc_cidr must have a prefix length from /16 to /24."
  }

  validation {
    # With a prefix of /16 or longer, the block is inside a range when its first address is.
    condition = try(anytrue([
      cidrhost("${cidrhost(var.vpc_cidr, 0)}/8", 0) == "10.0.0.0",
      cidrhost("${cidrhost(var.vpc_cidr, 0)}/12", 0) == "172.16.0.0",
      cidrhost("${cidrhost(var.vpc_cidr, 0)}/16", 0) == "192.168.0.0",
    ]), false)
    error_message = "vpc_cidr must be inside 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16."
  }
}

variable "azs" {
  description = "Availability zones to create subnets in, such as [\"eu-west-1a\", \"eu-west-1b\"]."
  type        = list(string)

  validation {
    condition     = length(var.azs) >= 2 && length(var.azs) <= 4
    error_message = "azs must list 2 to 4 availability zones."
  }

  validation {
    condition     = length(distinct(var.azs)) == length(var.azs)
    error_message = "azs must all be different."
  }

  validation {
    condition     = alltrue([for az in var.azs : can(regex("^[a-z]{2}(-[a-z]+)+-\\d+[a-f]$", az))])
    error_message = "Each AZ must be a name such as eu-west-1a that ends in a letter from a to f."
  }

  validation {
    condition     = length(distinct([for az in var.azs : try(substr(az, 0, length(az) - 1), az)])) == 1
    error_message = "azs must all be in the same region."
  }
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
