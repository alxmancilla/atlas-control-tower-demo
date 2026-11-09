output "msk_cluster_arn" {
  description = "ARN of the demo MSK cluster."
  value       = aws_msk_cluster.this.arn
}

output "msk_security_group_id" {
  description = "Security group attached to MSK broker ENIs."
  value       = aws_security_group.msk.id
}

output "kafka_bootstrap_servers" {
  description = "Private multi-VPC SASL/SCRAM bootstrap servers registered with Atlas Stream Processing."
  value       = aws_msk_cluster.this.bootstrap_brokers_vpc_connectivity_sasl_scram
}

output "stream_privatelink_endpoint_id" {
  description = "Atlas Stream Processing PrivateLink endpoint ID for the MSK cluster."
  value       = mongodbatlas_stream_privatelink_endpoint.msk.id
}

output "stream_kafka_connection_name" {
  description = "Set this non-secret label as ASP_KAFKA_CONNECTION in the demo .env."
  value       = mongodbatlas_stream_connection.kafka.connection_name
}

output "stream_atlas_sink_connection_name" {
  description = "Set this non-secret label as ASP_ATLAS_CONNECTION in the demo .env."
  value       = mongodbatlas_stream_connection.atlas_sink.connection_name
}

output "scram_secret_arn" {
  description = "ARN of the MSK-managed SCRAM credential secret; its value is never output."
  value       = aws_secretsmanager_secret.scram.arn
}