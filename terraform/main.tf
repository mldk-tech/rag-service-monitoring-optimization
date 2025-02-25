/**
 * Main Terraform configuration for RAG Service
 */

# Configure AWS provider
provider "aws" {
  region = var.aws_region
}

# Create VPC and networking resources
module "vpc" {
  source = "./modules/vpc"

  vpc_cidr             = var.vpc_cidr
  environment          = var.environment
  availability_zones   = var.availability_zones
  private_subnet_cidrs = var.private_subnet_cidrs
  public_subnet_cidrs  = var.public_subnet_cidrs
}

# Create security groups
module "security_groups" {
  source = "./modules/security_groups"

  vpc_id      = module.vpc.vpc_id
  environment = var.environment
}

# Create Elasticsearch domain
module "elasticsearch" {
  source = "./modules/elasticsearch"

  domain_name           = "rag-${var.environment}"
  elasticsearch_version = "7.10"
  instance_type         = var.elasticsearch_instance_type
  instance_count        = var.elasticsearch_instance_count
  ebs_volume_size       = var.elasticsearch_volume_size
  vpc_id                = module.vpc.vpc_id
  subnet_ids            = module.vpc.private_subnet_ids
  security_group_ids    = [module.security_groups.elasticsearch_sg_id]
  environment           = var.environment
}

# Create MongoDB Atlas cluster
module "mongodb" {
  source = "./modules/mongodb"

  project_id          = var.mongodb_project_id
  cluster_name        = "rag-${var.environment}"
  mongodb_version     = "5.0"
  provider_name       = "AWS"
  region_name         = var.aws_region
  instance_size       = var.mongodb_instance_size
  disk_size_gb        = var.mongodb_disk_size_gb
  num_shards          = var.mongodb_num_shards
  replication_factor  = var.mongodb_replication_factor
  backup_enabled      = true
  environment         = var.environment
}

# Create Redis ElastiCache cluster
module "redis" {
  source = "./modules/redis"

  cluster_id           = "rag-${var.environment}"
  node_type            = var.redis_node_type
  num_cache_nodes      = var.redis_num_cache_nodes
  parameter_group_name = "default.redis6.x"
  engine_version       = "6.x"
  port                 = 6379
  subnet_group_name    = "rag-${var.environment}-redis-subnet-group"
  security_group_ids   = [module.security_groups.redis_sg_id]
  subnet_ids           = module.vpc.private_subnet_ids
  environment          = var.environment
}

# Create ECR repository
module "ecr" {
  source = "./modules/ecr"

  repository_name = "rag-service-${var.environment}"
  environment     = var.environment
}

# Create ECS cluster
module "ecs_cluster" {
  source = "./modules/ecs_cluster"

  cluster_name = "rag-${var.environment}"
  environment  = var.environment
}

# Create ECS service
module "ecs_service" {
  source = "./modules/ecs_service"

  service_name        = "rag-service-${var.environment}"
  cluster_id          = module.ecs_cluster.cluster_id
  task_definition_arn = module.ecs_task.task_definition_arn
  desired_count       = var.ecs_service_desired_count
  launch_type         = "FARGATE"
  subnets             = module.vpc.private_subnet_ids
  security_groups     = [module.security_groups.ecs_service_sg_id]
  assign_public_ip    = false
  environment         = var.environment

  load_balancer = {
    target_group_arn = module.alb.target_group_arn
    container_name   = "rag-service"
    container_port   = 8000
  }
}

# Create ECS task definition
module "ecs_task" {
  source = "./modules/ecs_task"

  family                   = "rag-service-${var.environment}"
  execution_role_arn       = module.iam.ecs_execution_role_arn
  task_role_arn            = module.iam.ecs_task_role_arn
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.ecs_task_cpu
  memory                   = var.ecs_task_memory
  environment              = var.environment

  container_definitions = jsonencode([
    {
      name      = "rag-service"
      image     = "${var.container_registry}/${var.container_image}:${var.container_tag}"
      essential = true
      
      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        },
        {
          containerPort = 9090
          hostPort      = 9090
          protocol      = "tcp"
        }
      ]
      
      environment = [
        { name = "ENVIRONMENT", value = var.environment },
        { name = "ELASTICSEARCH_HOSTS", value = module.elasticsearch.endpoint },
        { name = "MONGODB_URI", value = module.mongodb.connection_string },
        { name = "REDIS_HOST", value = module.redis.endpoint },
        { name = "REDIS_PORT", value = "6379" },
        { name = "LOG_LEVEL", value = "INFO" }
      ]
      
      secrets = [
        { name = "API_KEY", valueFrom = module.secrets.api_key_arn }
      ]
      
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/rag-service-${var.environment}"
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
      
      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])
}

# Create IAM roles and policies
module "iam" {
  source = "./modules/iam"

  environment = var.environment
}

# Create Application Load Balancer
module "alb" {
  source = "./modules/alb"

  name               = "rag-${var.environment}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [module.security_groups.alb_sg_id]
  subnets            = module.vpc.public_subnet_ids
  vpc_id             = module.vpc.vpc_id
  
  target_groups = [
    {
      name     = "rag-${var.environment}-tg"
      port     = 8000
      protocol = "HTTP"
      health_check = {
        path                = "/health"
        port                = "traffic-port"
        healthy_threshold   = 3
        unhealthy_threshold = 3
        timeout             = 5
        interval            = 30
        matcher             = "200"
      }
    }
  ]
  
  http_tcp_listeners = [
    {
      port               = 80
      protocol           = "HTTP"
      target_group_index = 0
    }
  ]
  
  environment = var.environment
}

# Create CloudWatch logs
module "cloudwatch" {
  source = "./modules/cloudwatch"

  log_group_name  = "/ecs/rag-service-${var.environment}"
  retention_days  = 30
  environment     = var.environment
}

# Create Secrets Manager secrets
module "secrets" {
  source = "./modules/secrets"

  secret_name   = "rag-service-${var.environment}"
  environment   = var.environment
}

# Create Prometheus and Grafana monitoring
module "monitoring" {
  source = "./modules/monitoring"

  environment     = var.environment
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnet_ids
  security_groups = [module.security_groups.monitoring_sg_id]
}

# Create S3 bucket for logs and backups
module "s3" {
  source = "./modules/s3"

  bucket_name  = "rag-service-${var.environment}-data"
  environment  = var.environment
}

# Create CloudFront distribution for global access
module "cloudfront" {
  source = "./modules/cloudfront"

  alb_domain_name = module.alb.dns_name
  environment     = var.environment
}

# Output important information
output "api_endpoint" {
  description = "API endpoint URL"
  value       = "https://${module.cloudfront.domain_name}"
}

output "elasticsearch_endpoint" {
  description = "Elasticsearch endpoint"
  value       = module.elasticsearch.endpoint
}

output "mongodb_connection_string" {
  description = "MongoDB connection string"
  value       = module.mongodb.connection_string
  sensitive   = true
}

output "redis_endpoint" {
  description = "Redis endpoint"
  value       = module.redis.endpoint
} 