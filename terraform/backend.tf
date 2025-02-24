terraform {
  backend "s3" {
    bucket         = "rag-service-terraform-state"
    key            = "terraform.tfstate"
    region         = "us-west-2"
    dynamodb_table = "rag-service-terraform-locks"
    encrypt        = true
    kms_key_id     = "alias/terraform-bucket-key"

    # Enable state locking
    dynamodb_table = "rag-service-terraform-locks"

    # Enable versioning
    versioning {
      enabled = true
    }

    # Enable server-side encryption
    server_side_encryption_configuration {
      rule {
        apply_server_side_encryption_by_default {
          sse_algorithm = "aws:kms"
        }
      }
    }

    # Enable access logging
    logging {
      target_bucket = "rag-service-terraform-logs"
      target_prefix = "terraform-state/"
    }
  }
} 