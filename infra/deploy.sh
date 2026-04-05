#!/bin/bash
# infra/deploy.sh — Orquestador unificado: selecciona modo segun DEPLOY_TARGET
# Uso: DEPLOY_TARGET=local bash infra/deploy.sh
#      DEPLOY_TARGET=oci bash infra/deploy.sh

set -euo pipefail

DEPLOY_TARGET="${DEPLOY_TARGET:-local}"

echo "============================================================"
echo " CCP — Deploy (modo: ${DEPLOY_TARGET})"
echo "============================================================"

case "${DEPLOY_TARGET}" in
  local)
    echo ">>> Modo: Kind local"
    bash infra/setup.sh
    bash infra/build-and-load.sh
    kubectl apply -f k8s/
    echo ""
    echo ">>> Despliegue local completo."
    echo "    Ejecutar experimentos con: bash scripts/run_experiments.sh"
    ;;
  oci)
    echo ">>> Modo: OCI OKE"
    bash infra/oci/setup_oke.sh
    bash infra/oci/ocir_push.sh
    bash infra/oci/deploy_services.sh
    echo ""
    echo ">>> Despliegue OCI completo."
    echo "    Verificar con: bash infra/oci/verify_oci.sh"
    ;;
  *)
    echo "ERROR: DEPLOY_TARGET debe ser 'local' o 'oci'"
    echo "Uso: DEPLOY_TARGET=local bash infra/deploy.sh"
    echo "     DEPLOY_TARGET=oci bash infra/deploy.sh"
    exit 1
    ;;
esac
