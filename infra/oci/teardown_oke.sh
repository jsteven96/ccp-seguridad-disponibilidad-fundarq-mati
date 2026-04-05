#!/bin/bash
# infra/oci/teardown_oke.sh

set -euo pipefail

: "${OCI_COMPARTMENT_ID:?Variable OCI_COMPARTMENT_ID requerida}"

CLUSTER_NAME="ccp-experiment-oke"

echo ">>> Eliminando servicios..."
kubectl delete -k k8s/overlays/oci/ --ignore-not-found

echo ">>> Eliminando Helm releases..."
helm uninstall nats -n messaging --ignore-not-found 2>/dev/null || true
helm uninstall mongodb -n data --ignore-not-found 2>/dev/null || true

echo ">>> Eliminando PVCs..."
kubectl delete pvc --all -n data --ignore-not-found
kubectl delete pvc --all -n messaging --ignore-not-found

echo ">>> Eliminando namespaces..."
kubectl delete namespace ccp data messaging --ignore-not-found

CLUSTER_ID=$(oci ce cluster list \
  --compartment-id "${OCI_COMPARTMENT_ID}" \
  --name "${CLUSTER_NAME}" \
  --lifecycle-state ACTIVE \
  --query 'data[0].id' --raw-output)

if [ "${CLUSTER_ID}" != "null" ] && [ -n "${CLUSTER_ID}" ]; then
  echo ">>> Eliminando cluster OKE ${CLUSTER_ID}..."
  oci ce cluster delete \
    --cluster-id "${CLUSTER_ID}" \
    --force \
    --wait-for-state SUCCEEDED \
    --wait-for-state FAILED
  echo "    Cluster eliminado."
else
  echo "    Cluster no encontrado."
fi

echo ">>> Eliminando imagenes de OCIR..."
# Las imagenes se eliminan desde OCI Console o con:
# oci artifacts container image list --compartment-id ${OCI_COMPARTMENT_ID} --repository-name ccp
# oci artifacts container image delete --image-id <image-ocid> --force

echo ">>> Teardown completo."
