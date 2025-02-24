provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "rag-service"
      Environment = var.environment
      Terraform   = "true"
      ManagedBy   = "terraform"
    }
  }

  # Enable AWS provider features
  assume_role {
    role_arn = "arn:aws:iam::${var.account_id}:role/TerraformExecutionRole"
  }

  # Configure retry behavior
  retry_mode = "standard"
  max_retries = 3

  # Configure endpoints for AWS services
  endpoints {
    dynamodb = "dynamodb.${var.aws_region}.amazonaws.com"
    ec2      = "ec2.${var.aws_region}.amazonaws.com"
    ecs      = "ecs.${var.aws_region}.amazonaws.com"
    s3       = "s3.${var.aws_region}.amazonaws.com"
  }
}

provider "aws" {
  alias  = "us-east-1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "rag-service"
      Environment = var.environment
      Terraform   = "true"
      ManagedBy   = "terraform"
    }
  }
}

# Required for ACM certificates
provider "aws" {
  alias  = "acm"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "rag-service"
      Environment = var.environment
      Terraform   = "true"
      ManagedBy   = "terraform"
    }
  }
}

# Required for Route53
provider "aws" {
  alias  = "route53"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "rag-service"
      Environment = var.environment
      Terraform   = "true"
      ManagedBy   = "terraform"
    }
  }
}

# Configure required provider versions
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  required_version = ">= 1.0"
} 