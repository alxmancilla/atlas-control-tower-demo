variable "aws_region" {
  description = "AWS region of the existing Atlas Stream Processing workspace and the MSK cluster."
  type        = string
}

variable "atlas_project_id" {
  description = "Atlas project ID that owns the existing Stream Processing workspace and Atlas cluster."
  type        = string

  validation {
    condition     = can(regex("^[a-f0-9]{24}$", var.atlas_project_id))
    error_message = "atlas_project_id must be a 24-character lowercase hexadecimal Atlas project ID."
  }
}

variable "stream_workspace_name" {
  description = "Existing AWS Stream Processing workspace name. It must be in aws_region."
  type        = string
}

variable "atlas_cluster_name" {
  description = "Existing Atlas cluster name for the telemetry_rollups sink connection."
  type        = string
}

variable "vpc_id" {
  description = "Existing non-production VPC ID in aws_region for MSK broker ENIs."
  type        = string

  validation {
    condition     = can(regex("^vpc-", var.vpc_id))
    error_message = "vpc_id must start with vpc-."
  }
}

variable "msk_subnet_ids" {
  description = "Two or three existing private subnet IDs in separate Availability Zones within vpc_id."
  type        = list(string)

  validation {
    condition     = length(var.msk_subnet_ids) >= 2 && length(var.msk_subnet_ids) <= 3
    error_message = "Provide two or three MSK subnet IDs."
  }
}

variable "kafka_allowed_cidr_blocks" {
  description = "CIDRs allowed to reach SASL/SCRAM brokers on 9096. Include the ASP VPC CIDR supplied by Atlas and any approved in-VPC producer CIDRs."
  type        = list(string)

  validation {
    condition     = length(var.kafka_allowed_cidr_blocks) > 0
    error_message = "Provide at least the Atlas Stream Processing VPC CIDR returned by Atlas account details."
  }
}

variable "atlas_streams_principal_arn" {
  description = "AWS principal ARN returned by the Atlas Streams account-details API for this project and region."
  type        = string

  validation {
    condition     = can(regex("^arn:[^:]+:iam::[0-9]{12}:(root|role/.+)$", var.atlas_streams_principal_arn))
    error_message = "atlas_streams_principal_arn must be the full IAM principal ARN returned by Atlas."
  }
}

variable "name_prefix" {
  description = "Short demo-only prefix used to name resources."
  type        = string
  default     = "control-tower-demo"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,32}$", var.name_prefix))
    error_message = "name_prefix must be 3-33 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "kafka_version" {
  description = "Kafka version supported by MSK in aws_region. Check AWS before changing the default."
  type        = string
  default     = "3.6.0"
}

variable "broker_instance_type" {
  description = "MSK provisioned broker type. kafka.t3.small is not supported for multi-VPC connectivity."
  type        = string
  default     = "kafka.m5.large"
}

variable "broker_volume_size_gib" {
  description = "EBS data volume size per broker in GiB."
  type        = number
  default     = 100
}

variable "kafka_scram_username" {
  description = "Kafka SCRAM username stored in the demo-owned AWS Secrets Manager secret."
  type        = string
  default     = "control_tower_asp"
}

variable "kafka_scram_password" {
  description = "Strong Kafka SCRAM password. Supply through TF_VAR_kafka_scram_password; it is stored in encrypted Terraform state."
  type        = string
  sensitive   = true
}

variable "kafka_connection_name" {
  description = "Atlas Stream Processing Connection Registry name for the MSK source."
  type        = string
  default     = "controlTowerMskKafka"
}

variable "atlas_sink_connection_name" {
  description = "Atlas Stream Processing Connection Registry name for the Atlas sink."
  type        = string
  default     = "controlTowerAtlas"
}

variable "atlas_sink_db_role" {
  description = "Built-in Atlas database role used by ASP for the Atlas cluster sink."
  type        = string
  default     = "readWriteAnyDatabase"
}