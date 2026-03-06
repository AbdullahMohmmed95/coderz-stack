#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  Coderz Stack — Restore Script
#  Restores from a backup snapshot directory
#
#  Usage:
#    bash /opt/coderz/backup/restore.sh                        # uses latest
#    bash /opt/coderz/backup/restore.sh 2026-03-06_04-00-00   # specific snapshot
#    bash /opt/coderz/backup/restore.sh --list                 # list all snapshots
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

BACKUP_ROOT="/opt/coderz/backup/snapshots"

# ── List mode ─────────────────────────────────────────────────────
if [[ "${1:-}" == "--list" ]]; then
  echo "Available backups:"
  ls -lh "$BACKUP_ROOT" | grep -E "^d|^l" | awk '{print "  " $NF, $5}'
  exit 0
fi

# ── Resolve snapshot ──────────────────────────────────────────────
if [[ -n "${1:-}" && "$1" != "--"* ]]; then
  SNAPSHOT="$BACKUP_ROOT/$1"
else
  SNAPSHOT="$BACKUP_ROOT/latest"
fi

if [ ! -d "$SNAPSHOT" ] && [ ! -L "$SNAPSHOT" ]; then
  echo "ERROR: Snapshot not found: $SNAPSHOT"
  echo "Run with --list to see available backups"
  exit 1
fi

# Resolve symlink
SNAPSHOT=$(realpath "$SNAPSHOT")
TIMESTAMP=$(basename "$SNAPSHOT")

echo "╔══════════════════════════════════════════════════════╗"
echo "║       Coderz Stack — Restore                        ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Snapshot: $TIMESTAMP"
echo "║  From    : $SNAPSHOT"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "⚠️  This will OVERWRITE current data. Are you sure? (yes/no)"
read -r CONFIRM
[ "$CONFIRM" != "yes" ] && echo "Aborted." && exit 0

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { log "  ✓ $*"; }
fail() { log "  ✗ $*"; }
skip() { log "  - SKIP: $*"; }

banner() {
  echo ""
  echo "──────────────────────────────────────────"
  echo "  $*"
  echo "──────────────────────────────────────────"
}

# ─────────────────────────────────────────────────────────────────
# 1. PostgreSQL — Prefect DB
# ─────────────────────────────────────────────────────────────────
banner "1. PostgreSQL — Prefect DB"
FILE="$SNAPSHOT/postgres-prefect.sql.gz"
if [ -f "$FILE" ]; then
  if docker ps --format '{{.Names}}' | grep -q "^coderz-postgres$"; then
    zcat "$FILE" | docker exec -i coderz-postgres psql -U prefect -d prefect \
      --quiet 2>/dev/null && ok "Prefect DB restored" || fail "Prefect DB restore failed"
  else
    fail "coderz-postgres not running"
  fi
else
  skip "postgres-prefect.sql.gz not found in snapshot"
fi

# ─────────────────────────────────────────────────────────────────
# 2. PostgreSQL — CoderAPI DB
# ─────────────────────────────────────────────────────────────────
banner "2. PostgreSQL — CoderAPI DB"
FILE="$SNAPSHOT/postgres-coderapi.sql.gz"
if [ -f "$FILE" ]; then
  if docker ps --format '{{.Names}}' | grep -q "^coderz-db$"; then
    zcat "$FILE" | docker exec -i coderz-db psql -U coderapi -d coderapi \
      --quiet 2>/dev/null && ok "CoderAPI DB restored" || fail "CoderAPI DB restore failed"
  else
    fail "coderz-db not running"
  fi
else
  skip "postgres-coderapi.sql.gz not found in snapshot"
fi

# ─────────────────────────────────────────────────────────────────
# 3. Redis
# ─────────────────────────────────────────────────────────────────
banner "3. Redis"
FILE="$SNAPSHOT/redis.rdb"
if [ -f "$FILE" ]; then
  if docker ps --format '{{.Names}}' | grep -q "^coderz-redis$"; then
    docker exec coderz-redis redis-cli SHUTDOWN NOSAVE 2>/dev/null || true
    sleep 1
    docker cp "$FILE" coderz-redis:/data/dump.rdb 2>/dev/null
    docker start coderz-redis 2>/dev/null
    ok "Redis RDB restored"
  else
    fail "coderz-redis not running"
  fi
else
  skip "redis.rdb not found in snapshot"
fi

# ─────────────────────────────────────────────────────────────────
# 4. Grafana
# ─────────────────────────────────────────────────────────────────
banner "4. Grafana"
FILE="$SNAPSHOT/grafana/grafana-data.tar.gz"
if [ -f "$FILE" ]; then
  docker stop coderz-grafana 2>/dev/null || true
  docker run --rm \
    -v coderz_grafana-data:/data \
    -v "$SNAPSHOT/grafana":/backup \
    alpine sh -c "rm -rf /data/* && tar xzf /backup/grafana-data.tar.gz -C /data" 2>/dev/null
  docker start coderz-grafana 2>/dev/null
  ok "Grafana data volume restored"
else
  skip "grafana-data.tar.gz not found in snapshot"
fi

# ─────────────────────────────────────────────────────────────────
# 5. Elasticsearch
# ─────────────────────────────────────────────────────────────────
banner "5. Elasticsearch"
FILE="$SNAPSHOT/elasticsearch-data.tar.gz"
if [ -f "$FILE" ]; then
  docker stop coderz-elasticsearch 2>/dev/null || true
  docker run --rm \
    -v coderz_es-data:/data \
    -v "$SNAPSHOT":/backup \
    alpine sh -c "rm -rf /data/* && tar xzf /backup/elasticsearch-data.tar.gz -C /data" 2>/dev/null
  docker start coderz-elasticsearch 2>/dev/null
  ok "Elasticsearch data volume restored"
else
  skip "elasticsearch-data.tar.gz not found in snapshot"
fi

# ─────────────────────────────────────────────────────────────────
# 6. Configs
# ─────────────────────────────────────────────────────────────────
banner "6. Configs"
FILE="$SNAPSHOT/configs.tar.gz"
if [ -f "$FILE" ]; then
  # Backup current configs first just in case
  tar czf "/tmp/configs-pre-restore-$(date +%s).tar.gz" -C /opt/coderz configs 2>/dev/null || true
  tar xzf "$FILE" -C /opt/coderz 2>/dev/null
  ok "Configs restored (pre-restore backup saved to /tmp/)"
else
  skip "configs.tar.gz not found in snapshot"
fi

# ─────────────────────────────────────────────────────────────────
# 7. APISIX routes (re-apply via Admin API)
# ─────────────────────────────────────────────────────────────────
banner "7. APISIX Config"
APISIX_DIR="$SNAPSHOT/apisix-config"
if [ -d "$APISIX_DIR" ]; then
  for RESOURCE in routes upstreams services consumers; do
    FILE="$APISIX_DIR/${RESOURCE}.json"
    [ -f "$FILE" ] || continue
    python3 -c "
import json, subprocess, sys
data = json.load(open('$FILE'))
items = data.get('list', [])
for item in items:
    body = item.get('value', {})
    rid = body.get('id','')
    if not rid: continue
    cmd = ['curl','-sf','-X','PUT',
           f'http://localhost:30180/apisix/admin/${RESOURCE}/{rid}',
           '-H','X-API-KEY: coderz-apisix-admin-key-2024',
           '-H','Content-Type: application/json',
           '-d', json.dumps(body)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(f'  ${RESOURCE}/{rid}: {r.returncode}')
" 2>/dev/null && ok "APISIX $RESOURCE restored" || fail "APISIX $RESOURCE restore failed"
  done
else
  skip "apisix-config not found in snapshot"
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Restore Complete                                    ║"
echo "║  Snapshot: $TIMESTAMP"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Restart all services to apply:"
echo "  cd /opt/coderz && docker compose restart"
