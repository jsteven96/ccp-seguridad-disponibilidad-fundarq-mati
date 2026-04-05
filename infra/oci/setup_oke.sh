#!/bin/bash
# infra/oci/setup_oke.sh

set -euo pipefail

: "${OCI_REGION:?Variable OCI_REGION requerida}"
: "${OCI_COMPARTMENT_ID:?Variable OCI_COMPARTMENT_ID requerida}"
: "${OCI_TENANCY_NAMESPACE:?Variable OCI_TENANCY_NAMESPACE requerida}"

CLUSTER_NAME="ccp-experiment-oke"
K8S_VERSION="v1.28.2"
NODE_SHAPE="VM.Standard.E4.Flex"
NODE_OCPUS=2
NODE_MEMORY_GB=8
NODE_COUNT=3

echo ">>> [1/4] Creando VCN para OKE..."
# OKE Quick Create genera VCN + subnets automaticamente.
# Si se necesita control fino, usar oci network vcn create.

echo ">>> [2/4] Creando cluster OKE '${CLUSTER_NAME}'..."
oci ce cluster create \
  --compartment-id "${OCI_COMPARTMENT_ID}" \
  --name "${CLUSTER_NAME}" \
  --kubernetes-version "${K8S_VERSION}" \
  --type ENHANCED_CLUSTER \
  --endpoint-subnet-id "${ENDPOINT_SUBNET_ID}" \
  --service-lb-subnet-ids "[\"${LB_SUBNET_ID}\"]" \
  --wait-for-state SUCCEEDED \
  --wait-for-state FAILED

CLUSTER_ID=$(oci ce cluster list \
  --compartment-id "${OCI_COMPARTMENT_ID}" \
  --name "${CLUSTER_NAME}" \
  --lifecycle-state ACTIVE \
  --query 'data[0].id' --raw-output)

echo "    Cluster ID: ${CLUSTER_ID}"

echo ">>> [3/4] Creando node pool..."
oci ce node-pool create \
  --compartment-id "${OCI_COMPARTMENT_ID}" \
  --cluster-id "${CLUSTER_ID}" \
  --name "ccp-workers" \
  --kubernetes-version "${K8S_VERSION}" \
  --node-shape "${NODE_SHAPE}" \
  --node-shape-config "{\"ocpus\": ${NODE_OCPUS}, \"memoryInGBs\": ${NODE_MEMORY_GB}}" \
  --size "${NODE_COUNT}" \
  --node-image-id "${NODE_IMAGE_ID}" \
  --placement-configs "[{\"availabilityDomain\": \"${AD}\", \"subnetId\": \"${WORKER_SUBNET_ID}\"}]" \
  --wait-for-state SUCCEEDED \
  --wait-for-state FAILED

echo ">>> [4/4] Configurando kubeconfig..."
oci ce cluster create-kubeconfig \
  --cluster-id "${CLUSTER_ID}" \
  --file "${HOME}/.kube/config" \
  --region "${OCI_REGION}" \
  --token-version 2.0.0 \
  --kube-endpoint PUBLIC_ENDPOINT

kubectl get nodes
echo "    OKE cluster listo."
