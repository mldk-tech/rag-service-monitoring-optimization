/**
 * Outputs for Elasticsearch Module
 */

output "endpoint" {
  description = "Elasticsearch domain endpoint"
  value       = aws_elasticsearch_domain.main.endpoint
}

output "kibana_endpoint" {
  description = "Kibana endpoint"
  value       = aws_elasticsearch_domain.main.kibana_endpoint
}

output "domain_id" {
  description = "Elasticsearch domain ID"
  value       = aws_elasticsearch_domain.main.domain_id
}

output "domain_name" {
  description = "Elasticsearch domain name"
  value       = aws_elasticsearch_domain.main.domain_name
}

output "arn" {
  description = "Elasticsearch domain ARN"
  value       = aws_elasticsearch_domain.main.arn
} 