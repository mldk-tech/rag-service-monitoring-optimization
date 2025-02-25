/**
 * Outputs for Monitoring Module
 */

output "prometheus_endpoint" {
  description = "Prometheus endpoint"
  value       = "http://${aws_lb_target_group.prometheus.name}.${var.environment}.local:9090"
}

output "grafana_endpoint" {
  description = "Grafana endpoint"
  value       = "http://${aws_lb_target_group.grafana.name}.${var.environment}.local:3000"
}

output "otel_collector_endpoint" {
  description = "OpenTelemetry Collector endpoint"
  value       = "http://otel-collector.${var.environment}.local:4317"
}

output "grafana_admin_password" {
  description = "Grafana admin password"
  value       = aws_secretsmanager_secret_version.grafana_admin_password.secret_string
  sensitive   = true
}

output "prometheus_task_definition_arn" {
  description = "ARN of the Prometheus task definition"
  value       = aws_ecs_task_definition.prometheus.arn
}

output "grafana_task_definition_arn" {
  description = "ARN of the Grafana task definition"
  value       = aws_ecs_task_definition.grafana.arn
}

output "otel_collector_task_definition_arn" {
  description = "ARN of the OpenTelemetry Collector task definition"
  value       = aws_ecs_task_definition.otel_collector.arn
} 