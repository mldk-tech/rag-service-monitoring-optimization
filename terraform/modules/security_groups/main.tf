/**
 * Security Groups Module for RAG Service
 * Creates security groups for various components of the RAG service
 */

# Security group for Application Load Balancer
resource "aws_security_group" "alb" {
  name        = "rag-${var.environment}-alb-sg"
  description = "Security group for RAG service ALB"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTP from internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS from internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "rag-${var.environment}-alb-sg"
    Environment = var.environment
  }
}

# Security group for ECS service
resource "aws_security_group" "ecs_service" {
  name        = "rag-${var.environment}-ecs-service-sg"
  description = "Security group for RAG service ECS tasks"
  vpc_id      = var.vpc_id

  ingress {
    description     = "HTTP from ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Metrics from monitoring"
    from_port       = 9090
    to_port         = 9090
    protocol        = "tcp"
    security_groups = [aws_security_group.monitoring.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "rag-${var.environment}-ecs-service-sg"
    Environment = var.environment
  }
}

# Security group for Elasticsearch
resource "aws_security_group" "elasticsearch" {
  name        = "rag-${var.environment}-elasticsearch-sg"
  description = "Security group for RAG service Elasticsearch"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Elasticsearch from ECS service"
    from_port       = 9200
    to_port         = 9200
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_service.id]
  }

  ingress {
    description     = "Elasticsearch from ECS service"
    from_port       = 9300
    to_port         = 9300
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_service.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "rag-${var.environment}-elasticsearch-sg"
    Environment = var.environment
  }
}

# Security group for Redis
resource "aws_security_group" "redis" {
  name        = "rag-${var.environment}-redis-sg"
  description = "Security group for RAG service Redis"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Redis from ECS service"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_service.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "rag-${var.environment}-redis-sg"
    Environment = var.environment
  }
}

# Security group for monitoring (Prometheus/Grafana)
resource "aws_security_group" "monitoring" {
  name        = "rag-${var.environment}-monitoring-sg"
  description = "Security group for RAG service monitoring"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTP from internal"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  ingress {
    description = "HTTPS from internal"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  ingress {
    description = "Prometheus from internal"
    from_port   = 9090
    to_port     = 9090
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  ingress {
    description = "Grafana from internal"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "rag-${var.environment}-monitoring-sg"
    Environment = var.environment
  }
} 