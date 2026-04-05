#!/bin/bash
# infra/oci/deploy_services.sh

set -euo pipefail

: "${OCI_REGION:?Variable OCI_REGION requerida}"
: "${OCI_TENANCY_NAMESPACE:?Variable OCI_TENANCY_NAMESPACE requerida}"

BASE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo "============================================================"
echo " CCP — Deploy Services to OKE"
echo "============================================================"

# 1. Crear namespaces
echo ">>> [1/5] Creando namespaces..."
kubectl apply -f "${BASE_DIR}/infra/namespaces.yaml"

# 2. Crear imagePullSecret
echo ">>> [2/5] Creando imagePullSecret..."
bash "${BASE_DIR}/infra/oci/imagepullsecret.sh"

# 3. Sustituir variables de entorno en kustomization.yaml
#    Kustomize no soporta variables de entorno nativas; usamos envsubst
echo ">>> [3/5] Generando manifiestos con Kustomize..."
cd "${BASE_DIR}/k8s/overlays/oci"
envsubst < kustomization.yaml > kustomization-rendered.yaml
mv kustomization-rendered.yaml kustomization.yaml

# 4. Aplicar overlay
echo ">>> [4/5] Aplicando overlay OCI..."
kubectl apply -k "${BASE_DIR}/k8s/overlays/oci/"

# 5. Esperar rollouts
echo ">>> [5/5] Esperando rollouts..."
DEPLOYMENTS=(modulo-inventarios inv-standby monitor corrector validacion-cep modulo-seguridad log-auditoria)
for dep in "${DEPLOYMENTS[@]}"; do
  echo "    Waiting for ${dep}..."
  kubectl rollout status deployment/"${dep}" -n ccp --timeout=180s
done

echo ""
echo "============================================================"
echo " Servicios desplegados en OKE."
echo " Obtener IPs publicas con:"
echo "   kubectl get svc -n ccp"
echo "============================================================"
