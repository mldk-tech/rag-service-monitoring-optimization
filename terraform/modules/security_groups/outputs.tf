/**
 * Outputs for Security Groups Module
 */

output "alb_sg_id" {
  description = "ID of the ALB security group"
  value       = aws_security_group.alb.id
}

output "ecs_service_sg_id" {
  description = "ID of the ECS service security group"
  value       = aws_security_group.ecs_service.id
}

output "elasticsearch_sg_id" {
  description = "ID of the Elasticsearch security group"
  value       = aws_security_group.elasticsearch.id
}

output "redis_sg_id" {
  description = "ID of the Redis security group"
  value       = aws_security_group.redis.id
}

output "monitoring_sg_id" {
  description = "ID of the monitoring security group"
  value       = aws_security_group.monitoring.id
} 