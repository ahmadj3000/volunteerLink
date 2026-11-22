############################################
# VolunteerLink — Organization guardrails (SCPs)
# Applied from the MANAGEMENT account only (profile vl-mgmt).
# SCPs never affect the management account itself — only members.
############################################

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "vl-tfstate-565950323755"
    key          = "org/terraform.tfstate"
    region       = "us-east-1"
    profile      = "vl-mgmt"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region              = var.region
  profile             = "vl-mgmt"
  allowed_account_ids = [var.management_account_id]

  default_tags {
    tags = {
      Project   = "VolunteerLink"
      ManagedBy = "Terraform"
      Stack     = "org"
    }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "management_account_id" {
  type = string
}

variable "workload_account_ids" {
  description = "Member accounts the guardrails attach to (NGO, Volunteer)"
  type        = map(string)
}

locals {
  policies = {
    baseline = {
      description = "No leaving the org, no root user, no tampering with audit logs, backups or the admin role"
      file        = "${path.module}/policies/baseline.json"
    }
    region-lock = {
      description = "Only us-east-1 (global services exempt)"
      file        = "${path.module}/policies/region-lock.json"
    }
    cost-guardrails = {
      description = "Block budget traps: EC2 instances, NAT, public load balancers, OpenSearch, RDS, WAF, Bedrock non-embedding models (ECS Fargate and internal ALBs allowed)"
      file        = "${path.module}/policies/cost-guardrails.json"
    }
  }

  # every (policy, account) pair to attach
  attachments = {
    for pair in setproduct(keys(local.policies), keys(var.workload_account_ids)) :
    "${pair[0]}:${pair[1]}" => {
      policy  = pair[0]
      account = var.workload_account_ids[pair[1]]
    }
  }
}

resource "aws_organizations_policy" "scp" {
  for_each = local.policies

  name        = "vl-${each.key}"
  description = each.value.description
  type        = "SERVICE_CONTROL_POLICY"
  # re-encode to strip whitespace (SCPs are capped at 5,120 characters)
  content = jsonencode(jsondecode(file(each.value.file)))
}

resource "aws_organizations_policy_attachment" "scp" {
  for_each = local.attachments

  policy_id = aws_organizations_policy.scp[each.value.policy].id
  target_id = each.value.account
}

output "scp_ids" {
  value = { for k, p in aws_organizations_policy.scp : k => p.id }
}