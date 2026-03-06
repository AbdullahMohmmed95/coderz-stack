#!/usr/bin/env python3
"""
Configure APISIX routes with Redis cache fallback + observability.

How it works:
  1. Incoming request → APISIX rewrite phase (PRE_FUNCTION)
     - Check Redis for cached response
     - If HIT → return cached data immediately (works even if app is DOWN)
     - If MISS → forward to upstream
  2. Upstream returns response → body_filter phase (POST_FUNCTION)
     - If status 200 → store response body in Redis (TTL: 1 hour)
     - Future requests for the same URI will be served from Redis
  3. Global rules (applied to ALL routes):
     - loki-logger  → sends structured access logs to Loki
     - prometheus   → exposes per-route metrics on port 9091
     - request-id   → injects X-Request-Id header
"""

import json
import subprocess
import time
import sys

APISIX_ADMIN_URL = "http://127.0.0.1:30180"
ADMIN_KEY = "coderz-apisix-admin-key-2024"
REDIS_HOST = "redis-service.apisix.svc.cluster.local"
REDIS_PORT = 6379
CACHE_TTL = 3600  # seconds (1 hour)

# Loki endpoint (Dockerized, reachable from k8s via host IP)
LOKI_URL = "http://109.199.120.120:3100"

# ── Lua: Check Redis cache before proxying ──────────────────────────────────
PRE_FUNCTION = r"""
local function run(conf, ctx)
  local red = require("resty.redis"):new()
  red:set_timeouts(500, 500, 500)
  local ok, err = red:connect("redis-service.apisix.svc.cluster.local", 6379)
  if not ok then
    ngx.log(ngx.WARN, "[apisix-cache] Redis connect failed: ", err)
    return
  end

  local key = "cache:" .. ngx.var.request_method .. ":" .. ngx.var.uri
  local val, err = red:get(key)
  red:set_keepalive(10000, 10)

  if val and val ~= ngx.null then
    -- ✅ Cache HIT — serve from Redis (upstream may be down)
    ngx.header["Content-Type"] = "application/json; charset=utf-8"
    ngx.header["X-Cache-Status"] = "HIT"
    ngx.header["X-Cache-Key"] = key
    ngx.status = 200
    ngx.say(val)
    ngx.exit(200)
    return
  end

  -- Cache MISS — store key in context for POST_FUNCTION
  ngx.header["X-Cache-Status"] = "MISS"
  ctx._cache_key = key
end
return run
"""

# ── Lua: Store upstream response in Redis ──────────────────────────────────
POST_FUNCTION = r"""
local function run(conf, ctx)
  -- Skip if request was already served from cache (no key set)
  if not ctx._cache_key then return end

  -- Accumulate response body chunks
  local chunk = ngx.arg[1]
  local is_eof = ngx.arg[2]
  if chunk then
    ctx._resp_body = (ctx._resp_body or "") .. chunk
  end

  -- On last chunk, store in Redis if upstream returned 200
  if is_eof and ngx.status == 200 and ctx._resp_body and ctx._resp_body ~= "" then
    local red = require("resty.redis"):new()
    red:set_timeouts(1000, 1000, 1000)
    local ok, err = red:connect("redis-service.apisix.svc.cluster.local", 6379)
    if ok then
      red:setex(ctx._cache_key, 3600, ctx._resp_body)
      red:set_keepalive(10000, 10)
      ngx.log(ngx.INFO, "[apisix-cache] Stored: ", ctx._cache_key, " TTL=3600s")
    else
      ngx.log(ngx.WARN, "[apisix-cache] Redis store failed: ", err)
    end
  end
end
return run
"""

# ── Shared plugin config applied to every route ────────────────────────────
def cache_plugins(extra=None):
    plugins = {
        "serverless-pre-function": {
            "phase": "rewrite",
            "functions": [PRE_FUNCTION]
        },
        "serverless-post-function": {
            "phase": "body_filter",
            "functions": [POST_FUNCTION]
        },
        "cors": {
            "allow_origins": "*",
            "allow_methods": "GET,POST,PUT,DELETE,OPTIONS",
            "allow_headers": "Content-Type,Authorization"
        },
        "response-rewrite": {
            "headers": {
                "set": {
                    "X-Gateway": "APISIX",
                    "X-Cache-Backend": "Redis"
                }
            }
        }
    }
    if extra:
        plugins.update(extra)
    return plugins


def apisix_put(path, data):
    cmd = [
        "curl", "-s", "-w", "\n%{http_code}",
        "-X", "PUT",
        f"{APISIX_ADMIN_URL}/apisix/admin{path}",
        "-H", f"X-API-KEY: {ADMIN_KEY}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(data)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    lines = result.stdout.strip().split("\n")
    status = lines[-1].strip()
    body = "\n".join(lines[:-1])
    ok = status in ("200", "201")
    mark = "✓" if ok else "✗"
    print(f"  {mark} PUT {path}  [{status}]")
    if not ok:
        print(f"    Response: {body[:300]}")
    return ok


def wait_for_apisix(retries=60, delay=3):
    print("⏳ Waiting for APISIX Admin API to be ready...")
    for i in range(retries):
        try:
            result = subprocess.run(
                ["curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}",
                 f"{APISIX_ADMIN_URL}/apisix/admin/routes",
                 "-H", f"X-API-KEY: {ADMIN_KEY}"],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip() == "200":
                print("✅ APISIX is ready!\n")
                return True
        except Exception:
            pass
        print(f"   [{i+1}/{retries}] Not ready yet, retrying in {delay}s...")
        time.sleep(delay)
    print("❌ APISIX not ready after timeout.")
    return False


def configure():
    if not wait_for_apisix():
        sys.exit(1)

    print("── Global Rules ─────────────────────────────────────────")

    # Global rule: loki-logger + prometheus + request-id applied to ALL routes
    apisix_put("/global_rules/1", {
        "plugins": {
            # ─── Structured access log → Loki ───────────────────────
            "loki-logger": {
                "endpoint_addrs": [LOKI_URL],
                "log_format": {
                    "host":            "$hostname",
                    "client_ip":       "$remote_addr",
                    "method":          "$request_method",
                    "uri":             "$uri",
                    "query":           "$query_string",
                    "status":          "$status",
                    "latency_ms":      "$upstream_response_time",
                    "request_length":  "$request_length",
                    "bytes_sent":      "$bytes_sent",
                    "user_agent":      "$http_user_agent",
                    "route_name":      "$route_name",
                    "upstream_addr":   "$upstream_addr",
                    "cache_status":    "$sent_http_x_cache_status",
                    "request_id":      "$http_x_request_id"
                },
                "batch_max_size": 100,
                "inactive_timeout": 5,
                "timeout": 3,
                "keepalive": True,
                "include_req_body": False,
                "timeout": 3
            },
            # ─── Per-route Prometheus metrics ────────────────────────
            "prometheus": {
                "prefer_name": True
            },
            # ─── Inject unique request ID on every request ───────────
            "request-id": {
                "header_name": "X-Request-Id",
                "include_in_response": True
            }
        }
    })

    print("\n── Upstreams ────────────────────────────────────────────")

    # .NET API (Docker on host)
    apisix_put("/upstreams/1", {
        "name": "dotnet-api",
        "type": "roundrobin",
        "nodes": {"109.199.120.120:5050": 1},
        "scheme": "http",
        "timeout": {"connect": 3, "send": 5, "read": 10},
        "retries": 1
    })

    # Web API (Docker on host)
    apisix_put("/upstreams/2", {
        "name": "web-api",
        "type": "roundrobin",
        "nodes": {"109.199.120.120:8888": 1},
        "scheme": "http",
        "timeout": {"connect": 3, "send": 5, "read": 10},
        "retries": 1
    })

    # coderz-web (k3s ClusterIP service)
    apisix_put("/upstreams/3", {
        "name": "coderz-web",
        "type": "roundrobin",
        "nodes": {"coderz-web.coderz.svc.cluster.local:80": 1},
        "scheme": "http",
        "timeout": {"connect": 3, "send": 5, "read": 10},
        "retries": 1
    })

    print("\n── Routes ───────────────────────────────────────────────")

    # Route: /dotnet/* → .NET API (strip /dotnet prefix)
    apisix_put("/routes/1", {
        "name": "dotnet-api",
        "uri": "/dotnet/*",
        "upstream_id": "1",
        "plugins": cache_plugins({
            "proxy-rewrite": {
                "regex_uri": ["^/dotnet/(.*)", "/$1"]
            }
        })
    })

    # Route: /webapi/* → Web API
    apisix_put("/routes/2", {
        "name": "web-api",
        "uri": "/webapi/*",
        "upstream_id": "2",
        "plugins": cache_plugins({
            "proxy-rewrite": {
                "regex_uri": ["^/webapi/(.*)", "/$1"]
            }
        })
    })

    # Route: /web/* → coderz-web (k3s nginx)
    apisix_put("/routes/3", {
        "name": "coderz-web",
        "uri": "/web/*",
        "upstream_id": "3",
        "plugins": cache_plugins({
            "proxy-rewrite": {
                "regex_uri": ["^/web/(.*)", "/$1"]
            }
        })
    })

    # Route: GET /cache/keys — inspect all cached keys in Redis
    apisix_put("/routes/99", {
        "name": "cache-inspector",
        "uri": "/cache/keys",
        "methods": ["GET"],
        "plugins": {
            "serverless-pre-function": {
                "phase": "rewrite",
                "functions": [r"""
local function run(conf, ctx)
  local red = require("resty.redis"):new()
  red:set_timeouts(1000, 1000, 1000)
  local ok, err = red:connect("redis-service.apisix.svc.cluster.local", 6379)
  if not ok then
    ngx.status = 503
    ngx.say('{"error":"Redis unavailable","detail":"' .. tostring(err) .. '"}')
    ngx.exit(503)
    return
  end
  local keys, err = red:keys("cache:*")
  local result = {}
  if keys and type(keys) == "table" then
    for _, k in ipairs(keys) do
      local ttl = red:ttl(k)
      table.insert(result, {key=k, ttl=ttl})
    end
  end
  red:set_keepalive(10000, 10)
  ngx.header["Content-Type"] = "application/json"
  ngx.status = 200
  ngx.say(require("cjson").encode({
    total = #result,
    keys = result
  }))
  ngx.exit(200)
end
return run
"""]
            }
        }
    })

    print("\n── Summary ──────────────────────────────────────────────")
    print(f"🌐 APISIX Gateway : http://109.199.120.120:30080")
    print(f"🔧 Admin API      : http://109.199.120.120:30180")
    print(f"📊 Metrics        : http://109.199.120.120:30091/apisix/prometheus/metrics")
    print()
    print("Routes with Redis cache fallback:")
    print(f"  GET  http://109.199.120.120:30080/dotnet/<path>  → .NET API (5050)")
    print(f"  GET  http://109.199.120.120:30080/webapi/<path>  → Web API  (8888)")
    print(f"  GET  http://109.199.120.120:30080/web/<path>     → coderz-web (k3s)")
    print()
    print("Cache management:")
    print(f"  GET  http://109.199.120.120:30080/cache/keys     → list all cached keys")
    print()
    print("Global plugins on ALL routes:")
    print("  loki-logger  → structured access logs sent to Loki")
    print("  prometheus   → per-route metrics on :9091/apisix/prometheus/metrics")
    print("  request-id   → X-Request-Id header on every request/response")
    print()
    print("Log fields: client_ip | method | uri | query | status | latency_ms |")
    print("            bytes_sent | user_agent | route_name | upstream_addr | cache_status")


if __name__ == "__main__":
    configure()
