# Copied into the module by the grader for its own plans, which run with no
# network. Nothing is applied, so the fake keys never reach AWS.
provider "aws" {
  region                      = "eu-west-1"
  access_key                  = "grader"
  secret_key                  = "grader"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  skip_region_validation      = true
}
