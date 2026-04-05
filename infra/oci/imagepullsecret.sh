#!/bin/bash
# infra/oci/imagepullsecret.sh

set -euo pipefail

: "${OCI_REGION:?Variable OCI_REGION requerida}"
: "${OCIR_USERNAME:?Variable OCIR_USERNAME requerida}"
: "${OCIR_PASSWORD:?Variable OCIR_PASSWORD requerida}"

NAMESPACES=("ccp" "data" "messaging")
SECRET_NAME="ocir-secret"
REGISTRY="${OCI_REGION}.ocir.io"

for ns in "${NAMESPACES[@]}"; do
  echo ">>> Creando secret '${SECRET_NAME}' en namespace '${ns}'..."
  kubectl create secret docker-registry "${SECRET_NAME}" \
    --docker-server="${REGISTRY}" \
    --docker-username="${OCIR_USERNAME}" \
    --docker-password="${OCIR_PASSWORD}" \
    --docker-email="noreply@oci.com" \
    --namespace="${ns}" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo "    Secret creado en ${ns}."
done
