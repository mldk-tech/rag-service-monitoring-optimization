/**
 * Elasticsearch Module for RAG Service
 * Creates an Elasticsearch domain for vector search and document storage
 */

resource "aws_elasticsearch_domain" "main" {
  domain_name           = var.domain_name
  elasticsearch_version = var.elasticsearch_version

  cluster_config {
    instance_type            = var.instance_type
    instance_count           = var.instance_count
    zone_awareness_enabled   = var.instance_count > 1 ? true : false
    
    zone_awareness_config {
      availability_zone_count = var.instance_count > 1 ? (var.instance_count > 3 ? 3 : var.instance_count) : 0
    }
  }

  ebs_options {
    ebs_enabled = true
    volume_size = var.ebs_volume_size
    volume_type = "gp3"
  }

  encrypt_at_rest {
    enabled = true
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  advanced_options = {
    "rest.action.multi.allow_explicit_index" = "true"
    "indices.fielddata.cache.size"           = "25"
    "indices.query.bool.max_clause_count"    = "1024"
  }

  advanced_security_options {
    enabled                        = true
    internal_user_database_enabled = true
    
    master_user_options {
      master_user_name     = "admin"
      master_user_password = aws_secretsmanager_secret_version.elasticsearch_master_password.secret_string
    }
  }

  vpc_options {
    subnet_ids         = slice(var.subnet_ids, 0, var.instance_count > 1 ? (var.instance_count > 3 ? 3 : var.instance_count) : 1)
    security_group_ids = var.security_group_ids
  }

  snapshot_options {
    automated_snapshot_start_hour = 23
  }

  log_publishing_options {
    enabled                  = true
    log_type                 = "INDEX_SLOW_LOGS"
    cloudwatch_log_group_arn = aws_cloudwatch_log_group.elasticsearch_logs.arn
  }

  log_publishing_options {
    enabled                  = true
    log_type                 = "SEARCH_SLOW_LOGS"
    cloudwatch_log_group_arn = aws_cloudwatch_log_group.elasticsearch_logs.arn
  }

  log_publishing_options {
    enabled                  = true
    log_type                 = "ES_APPLICATION_LOGS"
    cloudwatch_log_group_arn = aws_cloudwatch_log_group.elasticsearch_logs.arn
  }

  tags = {
    Name        = "rag-${var.environment}-elasticsearch"
    Environment = var.environment
  }
}

# CloudWatch Log Group for Elasticsearch logs
resource "aws_cloudwatch_log_group" "elasticsearch_logs" {
  name              = "/aws/elasticsearch/${var.domain_name}"
  retention_in_days = 30

  tags = {
    Name        = "rag-${var.environment}-elasticsearch-logs"
    Environment = var.environment
  }
}

# Secret for Elasticsearch master password
resource "aws_secretsmanager_secret" "elasticsearch_master_password" {
  name        = "rag-${var.environment}-elasticsearch-master-password"
  description = "Master password for Elasticsearch domain"
  
  tags = {
    Name        = "rag-${var.environment}-elasticsearch-master-password"
    Environment = var.environment
  }
}

resource "aws_secretsmanager_secret_version" "elasticsearch_master_password" {
  secret_id     = aws_secretsmanager_secret.elasticsearch_master_password.id
  secret_string = var.master_password != "" ? var.master_password : random_password.elasticsearch_master_password[0].result
}

resource "random_password" "elasticsearch_master_password" {
  count   = var.master_password == "" ? 1 : 0
  length  = 16
  special = true
} 