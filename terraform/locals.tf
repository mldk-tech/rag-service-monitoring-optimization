locals {
  # Environment-specific configurations
  is_production = var.environment == "production"
  is_staging   = var.environment == "staging"
  is_dev       = var.environment == "dev"

  # Resource naming
  name_prefix = "rag-service-${var.environment}"
  
  # Common tags
  common_tags = merge(
    var.tags,
    {
      Project     = "rag-service"
      Environment = var.environment
      Terraform   = "true"
      ManagedBy   = "terraform"
    }
  )

  # Networking
  vpc_name = "${local.name_prefix}-vpc"
  
  # Load balancer configurations
  alb_name = "${local.name_prefix}-alb"
  
  # ECS configurations
  ecs_cluster_name = "${local.name_prefix}-cluster"
  ecs_service_name = "${local.name_prefix}-service"
  
  # Container configurations
  container_name = "rag-service"
  container_port = 8000
  container_image = "${var.container_registry}/${var.container_image}:${var.container_tag}"
  
  # Monitoring configurations
  log_group_name = "/ecs/${local.name_prefix}"
  metric_namespace = "RAGService/${var.environment}"
  
  # Security configurations
  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  
  # Scaling configurations
  scaling_policies = {
    cpu = {
      policy_name               = "${local.name_prefix}-cpu"
      predefined_metric_type    = "ECSServiceAverageCPUUtilization"
      target_value             = local.is_production ? 70 : 80
      scale_in_cooldown        = local.is_production ? 300 : 180
      scale_out_cooldown       = local.is_production ? 180 : 120
    }
    memory = {
      policy_name               = "${local.name_prefix}-memory"
      predefined_metric_type    = "ECSServiceAverageMemoryUtilization"
      target_value             = local.is_production ? 80 : 85
      scale_in_cooldown        = local.is_production ? 300 : 180
      scale_out_cooldown       = local.is_production ? 180 : 120
    }
  }

  # Health check configurations
  health_check = {
    path                = "/health"
    healthy_threshold   = local.is_production ? 3 : 2
    unhealthy_threshold = local.is_production ? 3 : 2
    timeout            = local.is_production ? 5 : 3
    interval           = local.is_production ? 30 : 15
    matcher            = "200"
  }

  # Backup configurations
  backup_schedule = local.is_production ? "cron(0 1 * * ? *)" : "cron(0 3 * * ? *)"
  
  # Alert configurations
  alert_thresholds = {
    cpu_utilization    = local.is_production ? 80 : 85
    memory_utilization = local.is_production ? 85 : 90
    error_rate        = local.is_production ? 1 : 5
    latency_p95       = local.is_production ? 1000 : 2000
  }

  # Cost allocation
  cost_tags = {
    CostCenter = local.is_production ? "prod-ml" : (local.is_staging ? "staging-ml" : "dev-ml")
    Project    = "rag-service"
  }

  # Security groups
  security_group_rules = {
    alb = {
      ingress = [
        {
          description = "HTTPS from internet"
          from_port   = 443
          to_port     = 443
          protocol    = "tcp"
          cidr_blocks = ["0.0.0.0/0"]
        }
      ]
      egress = [
        {
          description = "All outbound traffic"
          from_port   = 0
          to_port     = 0
          protocol    = "-1"
          cidr_blocks = ["0.0.0.0/0"]
        }
      ]
    }
    ecs = {
      ingress = [
        {
          description     = "HTTP from ALB"
          from_port      = local.container_port
          to_port        = local.container_port
          protocol       = "tcp"
          security_groups = ["${aws_security_group.alb.id}"]
        }
      ]
      egress = [
        {
          description = "All outbound traffic"
          from_port   = 0
          to_port     = 0
          protocol    = "-1"
          cidr_blocks = ["0.0.0.0/0"]
        }
      ]
    }
  }
} 