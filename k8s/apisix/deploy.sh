#!/bin/bash
# ─────────────────────────────────────────────────────────
#  Deploy Apache APISIX + Redis Cache on k3s
#  Usage: bash deploy.sh
# ─────────────────────────────────────────────────────────
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "╔══════════════════════════════════════════════════════╗"
echo "║  APISIX API Gateway + Redis Cache — k3s Deploy      ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Apply manifests ────────────────────────────────────────
echo "── Applying Kubernetes manifests ────────────────────────"
kubectl apply -f "$SCRIPT_DIR/00-namespace.yaml"
kubectl apply -f "$SCRIPT_DIR/01-redis.yaml"
kubectl apply -f "$SCRIPT_DIR/02-etcd.yaml"
kubectl apply -f "$SCRIPT_DIR/03-apisix-configmap.yaml"
kubectl apply -f "$SCRIPT_DIR/04-apisix.yaml"

echo ""
echo "── Waiting for pods ─────────────────────────────────────"
kubectl wait --for=condition=ready pod -l app=redis   -n apisix --timeout=120s
kubectl wait --for=condition=ready pod -l app=etcd    -n apisix --timeout=120s
kubectl wait --for=condition=ready pod -l app=apisix  -n apisix --timeout=180s

echo ""
echo "── Configuring APISIX routes ────────────────────────────"
python3 "$SCRIPT_DIR/configure-routes.py"

echo ""
echo "── Pod Status ───────────────────────────────────────────"
kubectl get pods -n apisix -o wide

echo ""
echo "── Services (NodePorts) ─────────────────────────────────"
kubectl get svc -n apisix
