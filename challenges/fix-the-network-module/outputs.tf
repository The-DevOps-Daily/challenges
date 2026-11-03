output "vpc_id" {
  description = "ID of the VPC."
  value       = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets, by AZ."
  value       = { for az, subnet in aws_subnet.public : az => subnet.id }
}

output "private_subnet_ids" {
  description = "IDs of the private subnets, by AZ."
  value       = { for az, subnet in aws_subnet.private : az => subnet.id }
}

output "public_subnet_cidrs" {
  description = "CIDR blocks of the public subnets, by AZ."
  value       = { for az, subnet in aws_subnet.public : az => subnet.cidr_block }
}

output "private_subnet_cidrs" {
  description = "CIDR blocks of the private subnets, by AZ."
  value       = { for az, subnet in aws_subnet.private : az => subnet.cidr_block }
}
