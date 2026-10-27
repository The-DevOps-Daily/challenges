variable "name" {
  description = "Name of the network. Used in the Name tag of every resource."
  type        = string
}

variable "environment" {
  description = "Environment the network is for: dev, staging or prod."
  type        = string
}

variable "vpc_cidr" {
  description = "IPv4 CIDR block of the VPC."
  type        = string
}

variable "azs" {
  description = "Availability zones to create subnets in, such as [\"eu-west-1a\", \"eu-west-1b\"]."
  type        = list(string)
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
