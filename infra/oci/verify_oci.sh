#!/bin/bash
# infra/oci/verify_oci.sh

set -euo pipefail

echo "============================================================"
echo " CCP — Verificacion del Despliegue OKE"
echo "============================================================"

echo ""
echo ">>> [1/5] Pods en namespace ccp..."
kubectl get pods -n ccp -o wide
echo ""

echo ">>> [2/5] Pods en namespace data..."
kubectl get pods -n data -o wide
echo ""

echo ">>> [3/5] Pods en namespace messaging..."
kubectl get pods -n messaging -o wide
echo ""

echo ">>> [4/5] Services con IPs externas..."
kubectl get svc -n ccp
echo ""

echo ">>> [5/5] Health checks via LoadBalancer IPs..."
INV_IP=$(kubectl get svc modulo-inventarios -n ccp -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
CEP_IP=$(kubectl get svc validacion-cep -n ccp -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

echo "    modulo-inventarios: http://${INV_IP}:8090/health"
curl -sf "http://${INV_IP}:8090/health" && echo " -> OK" || echo " -> FAIL"

echo "    validacion-cep: http://${CEP_IP}:8094/health"
curl -sf "http://${CEP_IP}:8094/health" && echo " -> OK" || echo " -> FAIL"

echo ""
echo "============================================================"
echo " Para ejecutar experimentos, usar:"
echo "   BASE_URL_INV=http://${INV_IP}:8090 BASE_URL_CEP=http://${CEP_IP}:8094 python scripts/validate_asrs.py"
echo "============================================================"
