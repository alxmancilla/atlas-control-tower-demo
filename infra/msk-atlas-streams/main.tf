locals {
  broker_count         = length(var.msk_subnet_ids)
  default_replication  = min(3, local.broker_count)
  min_in_sync_replicas = max(1, local.default_replication - 1)
  msk_cluster_name     = "${var.name_prefix}-msk"
  scram_secret_name    = "AmazonMSK_${var.name_prefix}_asp"
  log_group_name       = "/aws/msk/${var.name_prefix}"
  tags = {
    Application = "atlas-control-tower-demo"
    Component   = "atlas-stream-processing"
    ManagedBy   = "terraform"
    Environment = "demo"
  }
}

data "aws_subnet" "msk" {
  for_each = toset(var.msk_subnet_ids)
  id       = each.value
}

resource "terraform_data" "network_guardrails" {
  input = {
    subnet_ids = var.msk_subnet_ids
    vpc_id     = var.vpc_id
  }

  lifecycle {
    precondition {
      condition     = alltrue([for subnet in data.aws_subnet.msk : subnet.vpc_id == var.vpc_id])
      error_message = "Every msk_subnet_ids entry must belong to vpc_id."
    }
    precondition {
      condition     = length(distinct([for subnet in data.aws_subnet.msk : subnet.availability_zone])) == length(var.msk_subnet_ids)
      error_message = "MSK subnets must be in distinct Availability Zones."
    }
    precondition {
      condition     = alltrue([for subnet in data.aws_subnet.msk : !subnet.map_public_ip_on_launch])
      error_message = "MSK subnets must be private (map_public_ip_on_launch = false)."
    }
  }
}

resource "aws_kms_key" "scram" {
  description             = "${var.name_prefix} MSK SCRAM secret"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "scram" {
  name          = "alias/${var.name_prefix}-msk-scram"
  target_key_id = aws_kms_key.scram.key_id
}

resource "aws_secretsmanager_secret" "scram" {
  name                    = local.scram_secret_name
  description             = "SASL/SCRAM credential for the ${local.msk_cluster_name} demo cluster"
  kms_key_id              = aws_kms_key.scram.arn
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "scram" {
  secret_id = aws_secretsmanager_secret.scram.id
  secret_string = jsonencode({
    username = var.kafka_scram_username
    password = var.kafka_scram_password
  })
}

resource "aws_security_group" "msk" {
  name_prefix = "${var.name_prefix}-msk-"
  description = "Restricts ${local.msk_cluster_name} SASL/SCRAM broker access"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "msk_scram" {
  for_each          = toset(var.kafka_allowed_cidr_blocks)
  security_group_id = aws_security_group.msk.id
  cidr_ipv4         = each.value
  from_port         = 9096
  to_port           = 9096
  ip_protocol       = "tcp"
  description       = "Approved SASL/SCRAM client network"
}

resource "aws_vpc_security_group_egress_rule" "msk_all" {
  security_group_id = aws_security_group.msk.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "MSK broker egress"
}

resource "aws_cloudwatch_log_group" "msk" {
  name              = local.log_group_name
  retention_in_days = 14
}

resource "aws_msk_configuration" "this" {
  name = "${var.name_prefix}-msk-config"

  server_properties = <<-PROPERTIES
    auto.create.topics.enable=false
    default.replication.factor=${local.default_replication}
    min.insync.replicas=${local.min_in_sync_replicas}
    num.partitions=3
    allow.everyone.if.no.acl.found=false
  PROPERTIES
}

resource "aws_msk_cluster" "this" {
  cluster_name           = local.msk_cluster_name
  kafka_version          = var.kafka_version
  number_of_broker_nodes = local.broker_count

  broker_node_group_info {
    instance_type   = var.broker_instance_type
    client_subnets  = var.msk_subnet_ids
    security_groups = [aws_security_group.msk.id]

    storage_info {
      ebs_storage_info {
        volume_size = var.broker_volume_size_gib
      }
    }

    connectivity_info {
      vpc_connectivity {
        client_authentication {
          sasl {
            scram = true
          }
        }
      }
    }
  }

  client_authentication {
    sasl {
      scram = true
    }
  }

  configuration_info {
    arn      = aws_msk_configuration.this.arn
    revision = aws_msk_configuration.this.latest_revision
  }

  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.msk.name
      }
    }
  }

  depends_on = [terraform_data.network_guardrails]
}

resource "aws_msk_scram_secret_association" "this" {
  cluster_arn     = aws_msk_cluster.this.arn
  secret_arn_list = [aws_secretsmanager_secret.scram.arn]

  depends_on = [aws_secretsmanager_secret_version.scram]
}

resource "aws_msk_cluster_policy" "atlas_streams" {
  cluster_arn = aws_msk_cluster.this.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowAtlasStreamProcessingPrivateConnectivity"
      Effect = "Allow"
      Principal = {
        AWS = var.atlas_streams_principal_arn
      }
      Action = [
        "kafka:CreateVpcConnection",
        "kafka:GetBootstrapBrokers",
        "kafka:DescribeCluster",
        "kafka:DescribeClusterV2"
      ]
      Resource = aws_msk_cluster.this.arn
    }]
  })
}

resource "mongodbatlas_stream_privatelink_endpoint" "msk" {
  project_id            = var.atlas_project_id
  provider_name         = "AWS"
  vendor                = "MSK"
  arn                   = aws_msk_cluster.this.arn
  authentication_scheme = "SASL_SCRAM"

  depends_on = [aws_msk_cluster_policy.atlas_streams, aws_msk_scram_secret_association.this]
}

resource "mongodbatlas_stream_connection" "kafka" {
  project_id      = var.atlas_project_id
  workspace_name  = var.stream_workspace_name
  connection_name = var.kafka_connection_name
  type            = "Kafka"

  authentication = {
    mechanism = "SCRAM-512"
    username  = var.kafka_scram_username
    password  = var.kafka_scram_password
  }
  bootstrap_servers = aws_msk_cluster.this.bootstrap_brokers_vpc_connectivity_sasl_scram
  config = {
    "auto.offset.reset" = "latest"
  }
  security = {
    protocol = "SASL_SSL"
  }
  networking = {
    access = {
      type          = "PRIVATE_LINK"
      connection_id = mongodbatlas_stream_privatelink_endpoint.msk.id
    }
  }
}

resource "mongodbatlas_stream_connection" "atlas_sink" {
  project_id      = var.atlas_project_id
  workspace_name  = var.stream_workspace_name
  connection_name = var.atlas_sink_connection_name
  type            = "Cluster"
  cluster_name    = var.atlas_cluster_name
  db_role_to_execute = {
    role = var.atlas_sink_db_role
    type = "BUILT_IN"
  }
}