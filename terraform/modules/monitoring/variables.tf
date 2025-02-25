/**
 * Variables for Monitoring Module
 */

variable "environment" {
  description = "Environment name (e.g., dev, staging, prod)"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC"
  type        = string
}

variable "subnet_ids" {
  description = "List of subnet IDs for the monitoring components"
  type        = list(string)
}

variable "security_groups" {
  description = "List of security group IDs for the monitoring components"
  type        = list(string)
}

variable "cluster_id" {
  description = "ID of the ECS cluster"
  type        = string
}

variable "execution_role_arn" {
  description = "ARN of the ECS execution role"
  type        = string
}

variable "task_role_arn" {
  description = "ARN of the ECS task role"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
}

variable "alb_listener_arn" {
  description = "ARN of the ALB listener"
  type        = string
}

variable "grafana_admin_password" {
  description = "Admin password for Grafana (if empty, a random one will be generated)"
  type        = string
  default     = ""
  sensitive   = true
} 