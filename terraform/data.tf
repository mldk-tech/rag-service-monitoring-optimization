# Get AWS account ID
data "aws_caller_identity" "current" {}

# Get available AZs
data "aws_availability_zones" "available" {
  state = "available"
}

# Get Route53 zone
data "aws_route53_zone" "main" {
  name = var.domain_name
}

# Get ACM certificate
data "aws_acm_certificate" "app" {
  domain      = var.domain_name
  statuses    = ["ISSUED"]
  most_recent = true
  provider    = aws.acm
}

# Get KMS key for encryption
data "aws_kms_key" "app" {
  key_id = "alias/rag-service-${var.environment}"
}

# Get latest ECS-optimized AMI
data "aws_ami" "ecs_optimized" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-ecs-hvm-*-x86_64-ebs"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Get VPC endpoints for AWS services
data "aws_vpc_endpoint_service" "s3" {
  service      = "s3"
  service_type = "Gateway"
}

data "aws_vpc_endpoint_service" "ecr_api" {
  service = "ecr.api"
}

data "aws_vpc_endpoint_service" "ecr_dkr" {
  service = "ecr.dkr"
}

data "aws_vpc_endpoint_service" "logs" {
  service = "logs"
}

data "aws_vpc_endpoint_service" "secretsmanager" {
  service = "secretsmanager"
}

# Get IAM policy documents
data "aws_iam_policy_document" "ecs_task_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "ecs_task_execution" {
  statement {
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "secretsmanager:GetSecretValue"
    ]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "ecs_task_role" {
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
      "secretsmanager:GetSecretValue",
      "kms:Decrypt",
      "kms:GenerateDataKey"
    ]
    resources = [
      "arn:aws:s3:::${var.environment}-rag-service-*/*",
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.environment}/rag-service/*",
      data.aws_kms_key.app.arn
    ]
  }
}

# Get existing security groups if any
data "aws_security_groups" "existing" {
  filter {
    name   = "tag:Project"
    values = ["rag-service"]
  }

  filter {
    name   = "tag:Environment"
    values = [var.environment]
  }
} 