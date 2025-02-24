environment = "dev"
aws_region = "us-west-2"

# VPC Configuration
vpc_cidr = "10.0.0.0/16"
availability_zones = ["us-west-2a", "us-west-2b"]
private_subnet_cidrs = ["10.0.1.0/24", "10.0.2.0/24"]
public_subnet_cidrs = ["10.0.101.0/24", "10.0.102.0/24"]

# Container Configuration
container_registry = "ghcr.io"
container_image = "rag-service"
container_tag = "latest"

# ECS Configuration
task_cpu = 1024      # 1 vCPU
task_memory = 2048   # 2 GB
service_desired_count = 2
min_capacity = 2
max_capacity = 4

# Monitoring Configuration
enable_monitoring = true
log_retention_days = 14
backup_retention_days = 7

# Domain Configuration
domain_name = "dev-rag.example.com"
certificate_arn = "arn:aws:acm:us-west-2:123456789012:certificate/abc123"

# Alert Configuration
alert_email = "dev-alerts@example.com"

# Additional Tags
tags = {
  Environment = "dev"
  Team = "ml-platform"
  CostCenter = "dev-ml"
  Project = "rag-service"
} 