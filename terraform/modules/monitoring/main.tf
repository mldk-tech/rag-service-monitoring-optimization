/**
 * Monitoring Module for RAG Service
 * Creates monitoring infrastructure including Prometheus, Grafana, and OpenTelemetry
 */

# CloudWatch Log Group for monitoring components
resource "aws_cloudwatch_log_group" "monitoring" {
  name              = "/rag/${var.environment}/monitoring"
  retention_in_days = 30

  tags = {
    Name        = "rag-${var.environment}-monitoring-logs"
    Environment = var.environment
  }
}

# ECS Task Definition for OpenTelemetry Collector
resource "aws_ecs_task_definition" "otel_collector" {
  family                   = "rag-${var.environment}-otel-collector"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "otel-collector"
      image     = "otel/opentelemetry-collector-contrib:latest"
      essential = true
      
      portMappings = [
        {
          containerPort = 4317
          hostPort      = 4317
          protocol      = "tcp"
        },
        {
          containerPort = 4318
          hostPort      = 4318
          protocol      = "tcp"
        },
        {
          containerPort = 8889
          hostPort      = 8889
          protocol      = "tcp"
        }
      ]
      
      environment = [
        { name = "ENVIRONMENT", value = var.environment }
      ]
      
      mountPoints = [
        {
          sourceVolume  = "otel-collector-config"
          containerPath = "/etc/otelcol/config.yaml"
          readOnly      = true
        }
      ]
      
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.monitoring.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "otel-collector"
        }
      }
      
      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:13133/ || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])

  volume {
    name = "otel-collector-config"
    
    efs_volume_configuration {
      file_system_id = aws_efs_file_system.monitoring_config.id
      root_directory = "/"
    }
  }

  tags = {
    Name        = "rag-${var.environment}-otel-collector"
    Environment = var.environment
  }
}

# ECS Service for OpenTelemetry Collector
resource "aws_ecs_service" "otel_collector" {
  name            = "rag-${var.environment}-otel-collector"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.otel_collector.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_groups
    assign_public_ip = false
  }

  tags = {
    Name        = "rag-${var.environment}-otel-collector"
    Environment = var.environment
  }
}

# EFS File System for monitoring configuration
resource "aws_efs_file_system" "monitoring_config" {
  creation_token = "rag-${var.environment}-monitoring-config"
  encrypted      = true

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = {
    Name        = "rag-${var.environment}-monitoring-config"
    Environment = var.environment
  }
}

# EFS Mount Targets
resource "aws_efs_mount_target" "monitoring_config" {
  count           = length(var.subnet_ids)
  file_system_id  = aws_efs_file_system.monitoring_config.id
  subnet_id       = var.subnet_ids[count.index]
  security_groups = var.security_groups
}

# ECS Task Definition for Prometheus
resource "aws_ecs_task_definition" "prometheus" {
  family                   = "rag-${var.environment}-prometheus"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "prometheus"
      image     = "prom/prometheus:latest"
      essential = true
      
      portMappings = [
        {
          containerPort = 9090
          hostPort      = 9090
          protocol      = "tcp"
        }
      ]
      
      environment = [
        { name = "ENVIRONMENT", value = var.environment }
      ]
      
      mountPoints = [
        {
          sourceVolume  = "prometheus-config"
          containerPath = "/etc/prometheus"
          readOnly      = true
        },
        {
          sourceVolume  = "prometheus-data"
          containerPath = "/prometheus"
          readOnly      = false
        }
      ]
      
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.monitoring.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "prometheus"
        }
      }
      
      healthCheck = {
        command     = ["CMD-SHELL", "wget -q -O- http://localhost:9090/-/healthy || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])

  volume {
    name = "prometheus-config"
    
    efs_volume_configuration {
      file_system_id = aws_efs_file_system.monitoring_config.id
      root_directory = "/prometheus"
    }
  }

  volume {
    name = "prometheus-data"
    
    efs_volume_configuration {
      file_system_id = aws_efs_file_system.monitoring_data.id
      root_directory = "/"
    }
  }

  tags = {
    Name        = "rag-${var.environment}-prometheus"
    Environment = var.environment
  }
}

# ECS Service for Prometheus
resource "aws_ecs_service" "prometheus" {
  name            = "rag-${var.environment}-prometheus"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.prometheus.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_groups
    assign_public_ip = false
  }

  tags = {
    Name        = "rag-${var.environment}-prometheus"
    Environment = var.environment
  }
}

# EFS File System for monitoring data
resource "aws_efs_file_system" "monitoring_data" {
  creation_token = "rag-${var.environment}-monitoring-data"
  encrypted      = true

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = {
    Name        = "rag-${var.environment}-monitoring-data"
    Environment = var.environment
  }
}

# EFS Mount Targets for monitoring data
resource "aws_efs_mount_target" "monitoring_data" {
  count           = length(var.subnet_ids)
  file_system_id  = aws_efs_file_system.monitoring_data.id
  subnet_id       = var.subnet_ids[count.index]
  security_groups = var.security_groups
}

# ECS Task Definition for Grafana
resource "aws_ecs_task_definition" "grafana" {
  family                   = "rag-${var.environment}-grafana"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "grafana"
      image     = "grafana/grafana:latest"
      essential = true
      
      portMappings = [
        {
          containerPort = 3000
          hostPort      = 3000
          protocol      = "tcp"
        }
      ]
      
      environment = [
        { name = "ENVIRONMENT", value = var.environment },
        { name = "GF_SECURITY_ADMIN_USER", value = "admin" },
        { name = "GF_SECURITY_ADMIN_PASSWORD", value = aws_secretsmanager_secret_version.grafana_admin_password.secret_string }
      ]
      
      mountPoints = [
        {
          sourceVolume  = "grafana-config"
          containerPath = "/etc/grafana"
          readOnly      = true
        },
        {
          sourceVolume  = "grafana-data"
          containerPath = "/var/lib/grafana"
          readOnly      = false
        }
      ]
      
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.monitoring.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "grafana"
        }
      }
      
      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:3000/api/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])

  volume {
    name = "grafana-config"
    
    efs_volume_configuration {
      file_system_id = aws_efs_file_system.monitoring_config.id
      root_directory = "/grafana"
    }
  }

  volume {
    name = "grafana-data"
    
    efs_volume_configuration {
      file_system_id = aws_efs_file_system.monitoring_data.id
      root_directory = "/grafana"
    }
  }

  tags = {
    Name        = "rag-${var.environment}-grafana"
    Environment = var.environment
  }
}

# ECS Service for Grafana
resource "aws_ecs_service" "grafana" {
  name            = "rag-${var.environment}-grafana"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.grafana.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_groups
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.grafana.arn
    container_name   = "grafana"
    container_port   = 3000
  }

  tags = {
    Name        = "rag-${var.environment}-grafana"
    Environment = var.environment
  }
}

# Secret for Grafana admin password
resource "aws_secretsmanager_secret" "grafana_admin_password" {
  name        = "rag-${var.environment}-grafana-admin-password"
  description = "Admin password for Grafana"
  
  tags = {
    Name        = "rag-${var.environment}-grafana-admin-password"
    Environment = var.environment
  }
}

resource "aws_secretsmanager_secret_version" "grafana_admin_password" {
  secret_id     = aws_secretsmanager_secret.grafana_admin_password.id
  secret_string = var.grafana_admin_password != "" ? var.grafana_admin_password : random_password.grafana_admin_password[0].result
}

resource "random_password" "grafana_admin_password" {
  count   = var.grafana_admin_password == "" ? 1 : 0
  length  = 16
  special = true
}

# ALB Target Group for Grafana
resource "aws_lb_target_group" "grafana" {
  name        = "rag-${var.environment}-grafana-tg"
  port        = 3000
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"
  
  health_check {
    enabled             = true
    interval            = 30
    path                = "/api/health"
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    timeout             = 5
    matcher             = "200"
  }
  
  tags = {
    Name        = "rag-${var.environment}-grafana-tg"
    Environment = var.environment
  }
}

# ALB Listener Rule for Grafana
resource "aws_lb_listener_rule" "grafana" {
  listener_arn = var.alb_listener_arn
  priority     = 100
  
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.grafana.arn
  }
  
  condition {
    path_pattern {
      values = ["/grafana*"]
    }
  }
}

# ALB Target Group for Prometheus
resource "aws_lb_target_group" "prometheus" {
  name        = "rag-${var.environment}-prometheus-tg"
  port        = 9090
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"
  
  health_check {
    enabled             = true
    interval            = 30
    path                = "/-/healthy"
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    timeout             = 5
    matcher             = "200"
  }
  
  tags = {
    Name        = "rag-${var.environment}-prometheus-tg"
    Environment = var.environment
  }
}

# ALB Listener Rule for Prometheus
resource "aws_lb_listener_rule" "prometheus" {
  listener_arn = var.alb_listener_arn
  priority     = 110
  
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.prometheus.arn
  }
  
  condition {
    path_pattern {
      values = ["/prometheus*"]
    }
  }
} 