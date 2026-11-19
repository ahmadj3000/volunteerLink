############################################
# One-time bootstrap: the S3 bucket that stores Terraform state for
# the other stacks. Lives in the MANAGEMENT account. Uses LOCAL state
# (chicken-and-egg), so only Ahmad runs this, once.
############################################

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region              = "us-east-1"
  profile             = "vl-mgmt"
  allowed_account_ids = ["565950323755"]

  default_tags {
    tags = {
      Project   = "VolunteerLink"
      ManagedBy = "Terraform"
      Stack     = "bootstrap"
    }
  }
}

locals {
  bucket_name = "vl-tfstate-565950323755"
  # Teammates run the app stack with their NGO-account SSO role,
  # so the NGO and Volunteer accounts get read/write on state.
  writer_accounts = ["281780646139", "170813308606"]
}

resource "aws_s3_bucket" "state" {
  bucket = local.bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

data "aws_iam_policy_document" "state" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "WorkloadAccountsList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
    principals {
      type        = "AWS"
      identifiers = [for a in local.writer_accounts : "arn:aws:iam::${a}:root"]
    }
  }

  statement {
    sid = "WorkloadAccountsReadWriteAppState"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    # only the app stack's state + lock file, never org/ or bootstrap
    resources = ["${aws_s3_bucket.state.arn}/app/*"]
    principals {
      type        = "AWS"
      identifiers = [for a in local.writer_accounts : "arn:aws:iam::${a}:root"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket     = aws_s3_bucket.state.id
  policy     = data.aws_iam_policy_document.state.json
  depends_on = [aws_s3_bucket_public_access_block.state]
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}
