#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  Coderz Stack — Full Backup Script
#  Backs up: PostgreSQL (x2), Redis, Grafana, Prometheus, Loki,
#             Elasticsearch, Configs, etcd (k3s + APISIX)
#
#  Usage:
#    bash /opt/coderz/backup/backup.sh
#    bash /opt/coderz/backup/backup.sh --dest /mnt/nas
#    bash /opt/coderz/backup/backup.sh --keep 14
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────
BACKUP_ROOT="${BACKUP_DEST:-/opt/coderz/backup/snapshots}"
KEEP_DAYS="${KEEP_DAYS:-7}"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
DEST="$BACKUP_ROOT/$TIMESTAMP"
LOG="$DEST/backup.log"
MANIFEST="$DEST/manifest.json"

# Parse flags
while [[ $# -gt 0 ]]; do
  case $1 in
    --dest) BACKUP_ROOT="$2"; DEST="$BACKUP_ROOT/$TIMESTAMP"; shift 2 ;;
    --keep) KEEP_DAYS="$2"; shift 2 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

mkdir -p "$DEST"
exec > >(tee -a "$LOG") 2>&1

PASS=0; FAIL=0; SKIPPED=0
SIZES=()

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { log "  ✓ $*"; PASS=$((PASS+1)); }
fail() { log "  ✗ $*"; FAIL=$((FAIL+1)); }
skip() { log "  - SKIP: $*"; SKIPPED=$((SKIPPED+1)); }

size_of() { du -sh "$1" 2>/dev/null | cut -f1; }

banner() {
  echo ""
  echo "──────────────────────────────────────────"
  echo "  $*"
  echo "──────────────────────────────────────────"
}

# ── Header ────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════╗"
echo "║       Coderz Stack — Full Backup                    ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Timestamp : $TIMESTAMP          ║"
echo "║  Dest      : $DEST"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ─────────────────────────────────────────────────────────────────
# 1. PostgreSQL — Prefect DB
# ─────────────────────────────────────────────────────────────────
banner "1/8  PostgreSQL — Prefect DB"
if docker ps --format '{{.Names}}' | grep -q "^coderz-postgres$"; then
  FILE="$DEST/postgres-prefect.sql.gz"
  if docker exec coderz-postgres pg_dump -U prefect prefect | gzip > "$FILE"; then
    ok "postgres-prefect.sql.gz ($(size_of "$FILE"))"
  else
    fail "PostgreSQL prefect dump failed"
  fi
else
  skip "coderz-postgres not running"
fi

# ─────────────────────────────────────────────────────────────────
# 2. PostgreSQL — CoderAPI DB
# ─────────────────────────────────────────────────────────────────
banner "2/8  PostgreSQL — CoderAPI DB"
if docker ps --format '{{.Names}}' | grep -q "^coderz-db$"; then
  FILE="$DEST/postgres-coderapi.sql.gz"
  if docker exec coderz-db pg_dump -U coderapi coderapi | gzip > "$FILE"; then
    ok "postgres-coderapi.sql.gz ($(size_of "$FILE"))"
  else
    fail "PostgreSQL coderapi dump failed"
  fi
else
  skip "coderz-db not running"
fi

# ─────────────────────────────────────────────────────────────────
# 3. Redis
# ─────────────────────────────────────────────────────────────────
banner "3/8  Redis"
if docker ps --format '{{.Names}}' | grep -q "^coderz-redis$"; then
  FILE="$DEST/redis.rdb"
  docker exec coderz-redis redis-cli BGSAVE > /dev/null
  sleep 2
  if docker cp coderz-redis:/data/dump.rdb "$FILE" 2>/dev/null; then
    ok "redis.rdb ($(size_of "$FILE"))"
  else
    fail "Redis RDB copy failed"
  fi
else
  skip "coderz-redis not running"
fi

# ─────────────────────────────────────────────────────────────────
# 4. Grafana — dashboards export + data volume
# ─────────────────────────────────────────────────────────────────
banner "4/8  Grafana"
mkdir -p "$DEST/grafana"

# Export all dashboards via Grafana API
if docker ps --format '{{.Names}}' | grep -q "^coderz-grafana$"; then
  DASH_DIR="$DEST/grafana/dashboards"
  mkdir -p "$DASH_DIR"
  DASHBOARD_LIST=$(curl -sf "http://admin:coderz123@localhost:3000/api/search?type=dash-db" 2>/dev/null || echo "[]")
  COUNT=0
  echo "$DASHBOARD_LIST" | python3 -c "
import sys, json
for d in json.load(sys.stdin):
    print(d.get('uid',''), d.get('title','').replace('/','_').replace(' ','_'))
" 2>/dev/null | while read -r uid title; do
    [ -z "$uid" ] && continue
    DASH_JSON=$(curl -sf "http://admin:coderz123@localhost:3000/api/dashboards/uid/$uid" 2>/dev/null)
    if [ -n "$DASH_JSON" ]; then
      echo "$DASH_JSON" > "$DASH_DIR/${uid}__${title}.json"
      COUNT=$((COUNT+1))
    fi
  done
  TOTAL=$(ls "$DASH_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ')
  ok "Grafana dashboards exported: $TOTAL files"

  # Backup Grafana data volume
  FILE="$DEST/grafana/grafana-data.tar.gz"
  if docker run --rm \
    -v coderz_grafana-data:/data:ro \
    -v "$DEST/grafana":/backup \
    alpine tar czf /backup/grafana-data.tar.gz -C /data . 2>/dev/null; then
    ok "grafana-data.tar.gz ($(size_of "$FILE"))"
  else
    fail "Grafana volume backup failed"
  fi
else
  skip "coderz-grafana not running"
fi

# ─────────────────────────────────────────────────────────────────
# 5. Prometheus (TSDB snapshot)
# ─────────────────────────────────────────────────────────────────
banner "5/8  Prometheus"
if docker ps --format '{{.Names}}' | grep -q "^coderz-prometheus$"; then
  SNAP=$(curl -sf -XPOST "http://localhost:9090/api/v1/admin/tsdb/snapshot" 2>/dev/null \
         | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('name',''))" 2>/dev/null || echo "")
  if [ -n "$SNAP" ]; then
    FILE="$DEST/prometheus-snapshot.tar.gz"
    docker run --rm \
      -v coderz_prometheus-data:/data:ro \
      -v "$DEST":/backup \
      alpine tar czf "/backup/prometheus-snapshot.tar.gz" -C /data "snapshots/$SNAP" 2>/dev/null
    ok "prometheus-snapshot.tar.gz ($(size_of "$FILE"))"
  else
    # Fallback: backup full TSDB volume
    FILE="$DEST/prometheus-data.tar.gz"
    docker run --rm \
      -v coderz_prometheus-data:/data:ro \
      -v "$DEST":/backup \
      alpine tar czf /backup/prometheus-data.tar.gz -C /data . 2>/dev/null
    ok "prometheus-data.tar.gz ($(size_of "$FILE"))"
  fi
else
  skip "coderz-prometheus not running"
fi

# ─────────────────────────────────────────────────────────────────
# 6. Elasticsearch — index export
# ─────────────────────────────────────────────────────────────────
banner "6/8  Elasticsearch"
if docker ps --format '{{.Names}}' | grep -q "^coderz-elasticsearch$"; then
  ES_DIR="$DEST/elasticsearch"
  mkdir -p "$ES_DIR"

  # Get list of indices (skip system indices)
  INDICES=$(curl -sf "http://localhost:9200/_cat/indices?h=index" 2>/dev/null \
    | grep -v '^\.' | tr '\n' ' ' || echo "")

  if [ -n "$INDICES" ]; then
    for INDEX in $INDICES; do
      OUT="$ES_DIR/${INDEX}.json.gz"
      curl -sf "http://localhost:9200/${INDEX}/_search?size=10000&scroll=1m" \
        -H "Content-Type: application/json" \
        -d '{"query":{"match_all":{}}}' 2>/dev/null | gzip > "$OUT"
    done
    COUNT=$(ls "$ES_DIR"/*.json.gz 2>/dev/null | wc -l | tr -d ' ')
    ok "Elasticsearch: $COUNT indices exported"
  else
    ok "Elasticsearch: no user indices to export"
  fi

  # Also backup the full volume
  FILE="$DEST/elasticsearch-data.tar.gz"
  docker run --rm \
    -v coderz_es-data:/data:ro \
    -v "$DEST":/backup \
    alpine tar czf /backup/elasticsearch-data.tar.gz -C /data . 2>/dev/null
  ok "elasticsearch-data.tar.gz ($(size_of "$FILE"))"
else
  skip "coderz-elasticsearch not running"
fi

# ─────────────────────────────────────────────────────────────────
# 7. Configs directory
# ─────────────────────────────────────────────────────────────────
banner "7/8  Configs + Compose"
FILE="$DEST/configs.tar.gz"
tar czf "$FILE" \
  -C /opt/coderz \
  --exclude='configs/nginx/ssl/*.key' \
  configs docker-compose.yml 2>/dev/null
ok "configs.tar.gz ($(size_of "$FILE"))"

# ─────────────────────────────────────────────────────────────────
# 8. etcd — k3s state + APISIX routes
# ─────────────────────────────────────────────────────────────────
banner "8/8  etcd Snapshots"

# k3s etcd
if command -v k3s &>/dev/null && k3s kubectl get nodes &>/dev/null 2>&1; then
  FILE="$DEST/etcd-k3s.snapshot"
  if k3s etcd-snapshot save --name "$FILE" 2>/dev/null || \
     k3s etcd-snapshot --etcd-snapshot-dir "$DEST" 2>/dev/null; then
    ok "etcd-k3s.snapshot ($(size_of "$FILE"))"
  else
    # Alternative: backup k3s data dir
    FILE="$DEST/k3s-server-db.tar.gz"
    tar czf "$FILE" -C /var/lib/rancher/k3s/server db 2>/dev/null && \
      ok "k3s-server-db.tar.gz ($(size_of "$FILE"))" || fail "k3s etcd backup failed"
  fi
else
  skip "k3s not available"
fi

# APISIX etcd (runs in k8s pod)
if kubectl get pod -n apisix -l app=etcd &>/dev/null 2>&1; then
  ETCD_POD=$(kubectl get pod -n apisix -l app=etcd -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
  if [ -n "$ETCD_POD" ]; then
    FILE="$DEST/etcd-apisix.snapshot"
    if kubectl exec -n apisix "$ETCD_POD" -- \
        etcdctl snapshot save /tmp/apisix-snap.db \
        --endpoints=http://localhost:2379 2>/dev/null && \
       kubectl cp "apisix/$ETCD_POD:/tmp/apisix-snap.db" "$FILE" 2>/dev/null; then
      ok "etcd-apisix.snapshot ($(size_of "$FILE"))"
    else
      fail "APISIX etcd snapshot failed"
    fi
  fi

  # Also export all APISIX routes/upstreams/plugins as JSON (human-readable)
  APISIX_DIR="$DEST/apisix-config"
  mkdir -p "$APISIX_DIR"
  for RESOURCE in routes upstreams services consumers global_rules; do
    curl -sf "http://localhost:30180/apisix/admin/$RESOURCE" \
      -H "X-API-KEY: coderz-apisix-admin-key-2024" 2>/dev/null \
      > "$APISIX_DIR/${RESOURCE}.json" || true
  done
  ok "APISIX config exported (routes, upstreams, services, consumers, global_rules)"
else
  skip "APISIX etcd pod not running"
fi

# ─────────────────────────────────────────────────────────────────
# Write manifest
# ─────────────────────────────────────────────────────────────────
TOTAL_SIZE=$(du -sh "$DEST" | cut -f1)
cat > "$MANIFEST" <<EOF
{
  "timestamp": "$TIMESTAMP",
  "dest": "$DEST",
  "total_size": "$TOTAL_SIZE",
  "results": {
    "passed": $PASS,
    "failed": $FAIL,
    "skipped": $SKIPPED
  },
  "files": $(ls "$DEST" | python3 -c "import sys,json; files=sys.stdin.read().split(); print(json.dumps(files))")
}
EOF

# ─────────────────────────────────────────────────────────────────
# Update latest symlink
# ─────────────────────────────────────────────────────────────────
ln -sfn "$DEST" "$BACKUP_ROOT/latest"

# ─────────────────────────────────────────────────────────────────
# Rotate old backups
# ─────────────────────────────────────────────────────────────────
if [ "$KEEP_DAYS" -gt 0 ]; then
  DELETED=$(find "$BACKUP_ROOT" -maxdepth 1 -type d -name "20*" \
    -mtime +"$KEEP_DAYS" -exec rm -rf {} \; -print | wc -l)
  [ "$DELETED" -gt 0 ] && log "Rotated $DELETED backup(s) older than ${KEEP_DAYS}d"
fi

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Backup Complete                                     ║"
echo "╠══════════════════════════════════════════════════════╣"
printf "║  ✓ Passed  : %-38s║\n" "$PASS"
printf "║  ✗ Failed  : %-38s║\n" "$FAIL"
printf "║  - Skipped : %-38s║\n" "$SKIPPED"
printf "║  Total size: %-38s║\n" "$TOTAL_SIZE"
printf "║  Location  : %-38s║\n" "$DEST"
echo "╚══════════════════════════════════════════════════════╝"

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
