/**
 * Variables for Elasticsearch Module
 */

variable "domain_name" {
  description = "Name of the Elasticsearch domain"
  type        = string
}

variable "elasticsearch_version" {
  description = "Version of Elasticsearch to deploy"
  type        = string
  default     = "7.10"
}

variable "instance_type" {
  description = "Instance type for Elasticsearch nodes"
  type        = string
  default     = "t3.medium.elasticsearch"
}

variable "instance_count" {
  description = "Number of Elasticsearch nodes"
  type        = number
  default     = 2
}

variable "ebs_volume_size" {
  description = "Size of EBS volume per node in GB"
  type        = number
  default     = 20
}

variable "vpc_id" {
  description = "ID of the VPC"
  type        = string
}

variable "subnet_ids" {
  description = "List of subnet IDs for the Elasticsearch domain"
  type        = list(string)
}

variable "security_group_ids" {
  description = "List of security group IDs for the Elasticsearch domain"
  type        = list(string)
}

variable "master_password" {
  description = "Master password for Elasticsearch domain (if empty, a random one will be generated)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "environment" {
  description = "Environment name (e.g., dev, staging, prod)"
  type        = string
} 