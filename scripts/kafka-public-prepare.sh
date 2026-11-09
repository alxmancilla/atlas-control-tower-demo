#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KAFKA_PUBLIC_IP:-}" ]]; then
  echo "KAFKA_PUBLIC_IP must be set before preparing the public Kafka broker." >&2
  exit 1
fi

require_password() {
  local name="$1"
  local value="${!name:-}"
  if [[ -z "$value" ]]; then
    if [[ ! -t 0 ]]; then
      echo "${name} must be provided by a protected CI environment variable when stdin is not a terminal." >&2
      exit 1
    fi
    read -r -s -p "${name}: " value
    printf '\n' >&2
  fi
  if [[ -z "$value" ]]; then
    echo "${name} cannot be empty." >&2
    exit 1
  fi
  printf -v "$name" '%s' "$value"
}

require_password KAFKA_PUBLIC_ADMIN_PASSWORD
require_password KAFKA_PUBLIC_PRODUCER_PASSWORD
require_password KAFKA_PUBLIC_ASP_PASSWORD

if ! [[ "$KAFKA_PUBLIC_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "KAFKA_PUBLIC_IP must be an IPv4 address." >&2
  exit 1
fi

secrets_dir="${KAFKA_PUBLIC_SECRETS_DIR:-.local/kafka-public}"
if [[ -e "$secrets_dir" ]]; then
  echo "Refusing to replace existing public Kafka material at ${secrets_dir}." >&2
  exit 1
fi

command -v openssl >/dev/null || { echo "openssl is required." >&2; exit 1; }
command -v keytool >/dev/null || { echo "keytool from a Java Development Kit is required." >&2; exit 1; }
umask 077
mkdir -p "$secrets_dir"

openssl rand -base64 36 >"$secrets_dir/keystore-password"
openssl rand -base64 36 >"$secrets_dir/truststore-password"
openssl req -x509 -newkey rsa:4096 -nodes -sha256 -days 30 \
  -keyout "$secrets_dir/broker.key" \
  -out "$secrets_dir/broker.crt" \
  -subj "/CN=control-tower-kafka" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" \
  -addext "subjectAltName=IP:${KAFKA_PUBLIC_IP},DNS:kafka"
openssl pkcs12 -export -name kafka \
  -inkey "$secrets_dir/broker.key" \
  -in "$secrets_dir/broker.crt" \
  -out "$secrets_dir/kafka.keystore.p12" \
  -passout "file:$secrets_dir/keystore-password"
keytool -importcert -noprompt \
  -alias control-tower-kafka \
  -file "$secrets_dir/broker.crt" \
  -keystore "$secrets_dir/kafka.truststore.p12" \
  -storetype PKCS12 \
  -storepass:file "$secrets_dir/truststore-password" >/dev/null

cat >"$secrets_dir/broker_jaas.conf" <<EOF
KafkaServer {
  org.apache.kafka.common.security.plain.PlainLoginModule required
  username="admin"
  password="${KAFKA_PUBLIC_ADMIN_PASSWORD}"
  user_admin="${KAFKA_PUBLIC_ADMIN_PASSWORD}"
  user_producer="${KAFKA_PUBLIC_PRODUCER_PASSWORD}"
  user_asp="${KAFKA_PUBLIC_ASP_PASSWORD}";
};
EOF

write_client_properties() {
  local username="$1"
  local password="$2"
  local destination="$3"
  cat >"$destination" <<EOF
security.protocol=SASL_SSL
sasl.mechanism=PLAIN
sasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username="${username}" password="${password}";
ssl.truststore.type=PKCS12
ssl.truststore.location=/etc/kafka/secrets/kafka.truststore.p12
ssl.truststore.password=$(<"$secrets_dir/truststore-password")
EOF
}

write_client_properties admin "$KAFKA_PUBLIC_ADMIN_PASSWORD" "$secrets_dir/admin.properties"
write_client_properties producer "$KAFKA_PUBLIC_PRODUCER_PASSWORD" "$secrets_dir/producer.properties"
write_client_properties asp "$KAFKA_PUBLIC_ASP_PASSWORD" "$secrets_dir/asp.properties"
chmod 600 "$secrets_dir"/*

echo "Prepared public Kafka TLS material in ${secrets_dir}."
echo "Use broker.crt as the trusted broker certificate in Atlas; it expires in 30 days."
echo "Keep all other files private and do not commit or paste their contents."