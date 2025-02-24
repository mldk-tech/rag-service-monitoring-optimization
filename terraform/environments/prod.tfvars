environment = "production"
aws_region = "us-west-2"

# VPC Configuration
vpc_cidr = "10.0.0.0/16"
availability_zones = ["us-west-2a", "us-west-2b", "us-west-2c"]
private_subnet_cidrs = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
public_subnet_cidrs = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

# Container Configuration
container_registry = "ghcr.io"
container_image = "rag-service"
container_tag = "stable"

# ECS Configuration
task_cpu = 2048      # 2 vCPU
task_memory = 4096   # 4 GB
service_desired_count = 3
min_capacity = 3
max_capacity = 10

# Monitoring Configuration
enable_monitoring = true
log_retention_days = 90
backup_retention_days = 30

# Domain Configuration
domain_name = "rag.example.com"
certificate_arn = "arn:aws:acm:us-west-2:123456789012:certificate/def456"

# Alert Configuration
alert_email = "prod-alerts@example.com"

# Additional Tags
tags = {
  Environment = "production"
  Team = "ml-platform"
  CostCenter = "prod-ml"
  Project = "rag-service"
  Compliance = "required"
  DataClassification = "confidential"
} 