#!/bin/bash
# infra/oci/ocir_push.sh

set -euo pipefail

: "${OCI_REGION:?Variable OCI_REGION requerida}"
: "${OCI_TENANCY_NAMESPACE:?Variable OCI_TENANCY_NAMESPACE requerida}"

BASE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
OCIR_PREFIX="${OCI_REGION}.ocir.io/${OCI_TENANCY_NAMESPACE}/ccp"
TAG="latest"

# Mapeo de servicios: directorio → nombre de imagen
declare -A SERVICES=(
  ["modulo_inventarios"]="modulo-inventarios"
  ["monitor"]="monitor"
  ["corrector"]="corrector"
  ["validacion_cep"]="validacion-cep"
  ["modulo_seguridad"]="modulo-seguridad"
  ["log_auditoria"]="log-auditoria"
)

echo "============================================================"
echo " CCP — Build & Push to OCIR: ${OCIR_PREFIX}"
echo "============================================================"

for dir in "${!SERVICES[@]}"; do
  image_name="${SERVICES[$dir]}"
  local_tag="ccp/${image_name}:${TAG}"
  remote_tag="${OCIR_PREFIX}/${image_name}:${TAG}"

  echo ""
  echo ">>> Building ${local_tag} from services/${dir}/..."
  docker build -t "${local_tag}" "${BASE_DIR}/services/${dir}/"

  echo ">>> Tagging as ${remote_tag}..."
  docker tag "${local_tag}" "${remote_tag}"

  echo ">>> Pushing ${remote_tag}..."
  docker push "${remote_tag}"

  echo "    Done: ${image_name}"
done

echo ""
echo "============================================================"
echo " Todas las imagenes subidas a OCIR."
echo " Verificar con: oci artifacts container image list --compartment-id \${OCI_COMPARTMENT_ID}"
echo "============================================================"
