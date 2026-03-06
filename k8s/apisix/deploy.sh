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
kubectl apply -f "$SCRIPT_DIR/05-redis-exporter.yaml"
kubectl apply -f "$SCRIPT_DIR/06-dashboard.yaml"

echo ""
echo "── Waiting for pods ─────────────────────────────────────"
kubectl wait --for=condition=ready pod -l app=redis            -n apisix --timeout=120s
kubectl wait --for=condition=ready pod -l app=etcd             -n apisix --timeout=120s
kubectl wait --for=condition=ready pod -l app=apisix           -n apisix --timeout=180s
kubectl wait --for=condition=ready pod -l app=apisix-dashboard -n apisix --timeout=120s

echo ""
echo "── Configuring APISIX routes ────────────────────────────"
python3 "$SCRIPT_DIR/configure-routes.py"

echo ""
echo "── Pod Status ───────────────────────────────────────────"
kubectl get pods -n apisix -o wide

echo ""
echo "── Services (NodePorts) ─────────────────────────────────"
kubectl get svc -n apisix

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Access Points                                       ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Gateway    http://109.199.120.120:30080             ║"
echo "║  Admin API  http://109.199.120.120:30180             ║"
echo "║  Dashboard  http://109.199.120.120:30900             ║"
echo "║  Metrics    http://109.199.120.120:30091/apisix/...  ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Dashboard login: admin / coderz123"
echo ""
echo "Run smoke tests:"
echo "  kubectl apply -f $SCRIPT_DIR/07-smoke-test-job.yaml"
echo "  kubectl logs -f job/apisix-smoke-test -n apisix"
