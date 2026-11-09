# Demo MSK to Atlas Stream Processing

This Terraform root creates demo-scoped AWS MSK infrastructure and the Atlas Stream
Processing Connection Registry entries required by the telemetry-rollup extension.
It uses MSK **multi-VPC private connectivity**; it does not make Kafka public or
create a generic VPC endpoint service.

## What it creates

- A provisioned MSK cluster with TLS and SASL/SCRAM only.
- An MSK configuration that disables automatic topic creation and requires Kafka ACLs.
- A security group limited to supplied private CIDRs on port `9096`.
- A customer-managed KMS key and `AmazonMSK_` Secrets Manager SCRAM credential.
- An MSK cluster policy for the AWS principal used by Atlas Stream Processing.
- An Atlas Stream Processing MSK PrivateLink endpoint plus Kafka and Atlas Cluster
  connection-registry entries in an existing workspace.

It does **not** create an Atlas project, an Atlas cluster, a Stream Processing
workspace, Kafka topics/ACLs, a processor, or a remote Terraform state backend.

## Before applying

1. Use a non-production AWS VPC and two or three **private** subnets in distinct AZs.
2. Create the Stream Processing workspace on **AWS in the same region**.
3. Obtain the Stream Processing AWS principal ARN and VPC CIDR from Atlas account
   details for the project and region. Use the principal for
   `atlas_streams_principal_arn`; add the CIDR to `kafka_allowed_cidr_blocks`.
4. Authenticate AWS with your normal CLI profile or environment. Give the Terraform
   identity permission to create the listed MSK, EC2 security-group, KMS, Secrets
   Manager, CloudWatch Logs, and Atlas resources.
5. Supply Atlas provider credentials only via its supported environment variables,
   such as `MONGODB_ATLAS_PUBLIC_KEY` and `MONGODB_ATLAS_PRIVATE_KEY`.

## Plan-first setup

Copy the safe placeholder file, fill only non-secret values, and supply the password
from your terminal or CI secret store. The password is marked sensitive in output but
is stored by both Terraform resources in Terraform state; use encrypted, access-
controlled remote state before applying outside a disposable demo account.

```bash
cd infra/msk-atlas-streams
cp terraform.tfvars.example terraform.tfvars
export TF_VAR_kafka_scram_password='use-a-generated-secret-here'
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

MSK creation and enabling multi-VPC connectivity can take tens of minutes. The
provider waits for each resource to become ready. Do not run `apply` again while MSK
reports an update in progress.

## Post-provisioning steps

The output connection names are the non-secret values for `.env`:

```text
ASP_KAFKA_CONNECTION=controlTowerMskKafka
ASP_ATLAS_CONNECTION=controlTowerAtlas
ASP_TELEMETRY_TOPIC=control-tower-telemetry
```

Because the cluster enforces ACLs, use an approved Kafka admin host inside an allowed
network to create `control-tower-telemetry` and grant the SCRAM user only the topic,
consumer-group, and transactional permissions needed by the demo producer and ASP
consumer. Never enable `allow.everyone.if.no.acl.found` or open the broker security
group to the internet. Then deploy the reviewed processor from
`src/services/streamProcessing.js` as described in
[`../../docs/atlas-stream-processing.md`](../../docs/atlas-stream-processing.md).

## Teardown

Stop the Atlas Stream Processing processor first. Then destroy this Terraform root
from the exact directory and review the destroy plan before confirming:

```bash
terraform plan -destroy -out=destroy.tfplan
terraform apply destroy.tfplan
```

This deletes the MSK cluster, PrivateLink endpoint, connection-registry entries,
security group, log group, KMS key (after its seven-day deletion window), and SCRAM
secret (after its seven-day recovery window). It does not delete your Atlas project,
Atlas cluster, Stream Processing workspace, VPC, subnets, or the local Docker Kafka
container.