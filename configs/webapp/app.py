#!/usr/bin/env python3
"""
Coderz Web API — FastAPI REST application with Prometheus metrics,
structured JSON request logging (→ Filebeat → Logstash → Kibana),
and Redis response caching.
"""
import asyncio, json, os, random, sys, time, traceback
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse
from prometheus_client import (
    Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
)
import uvicorn
import redis.asyncio as aioredis

app = FastAPI(title="Coderz Web API", version="1.0.0", docs_url="/swagger")

# ── Redis client ──────────────────────────────────────────────────────────────

REDIS_URL  = os.getenv("REDIS_URL", "redis://localhost:6379")
_redis: aioredis.Redis | None = None

async def get_redis() -> aioredis.Redis | None:
    global _redis
    if _redis is None:
        try:
            _redis = aioredis.from_url(REDIS_URL, decode_responses=True, socket_timeout=1)
            await _redis.ping()
        except Exception:
            _redis = None
    return _redis

# ── Prometheus Metrics ────────────────────────────────────────────────────────

REQUEST_TOTAL = Counter(
    "http_requests_total", "Total HTTP requests",
    ["method", "endpoint", "status_code"]
)
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
ACTIVE_REQUESTS = Gauge("http_active_requests", "Currently active HTTP requests")
ERROR_TOTAL = Counter(
    "http_errors_total", "Total HTTP errors",
    ["endpoint", "error_type"]
)
DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds", "Simulated DB query duration",
    ["query_type"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
REQUEST_SIZE = Histogram(
    "http_request_size_bytes", "HTTP request body size",
    ["endpoint"],
    buckets=[64, 256, 1024, 4096, 16384]
)

# ── Sample Data ───────────────────────────────────────────────────────────────

_rng = random.Random(42)
USERS = [
    {
        "id": i,
        "name": f"User {i}",
        "email": f"user{i}@coderz.local",
        "role": _rng.choice(["admin", "user", "viewer"]),
        "active": _rng.choice([True, True, True, False]),
        "created_at": f"2024-{_rng.randint(1,12):02d}-{_rng.randint(1,28):02d}"
    }
    for i in range(1, 101)
]
PRODUCTS = [
    {
        "id": i,
        "name": f"Product {i}",
        "category": _rng.choice(["Electronics", "Software", "Hardware", "Services"]),
        "price": round(_rng.uniform(9.99, 999.99), 2),
        "stock": _rng.randint(0, 500),
        "sku": f"SKU-{i:05d}"
    }
    for i in range(1, 201)
]

# ── Helpers ───────────────────────────────────────────────────────────────────

async def simulate_db(min_ms: float, max_ms: float, query_type: str = "SELECT"):
    delay = random.uniform(min_ms, max_ms) / 1000.0
    start = time.time()
    await asyncio.sleep(delay)
    DB_QUERY_DURATION.labels(query_type=query_type).observe(time.time() - start)

def _emit_log(
    level: str,
    method: str,
    path: str,
    query: str,
    client_ip: str,
    status: int,
    duration_ms: int,
    user_agent: str = "",
    error_type: str | None = None,
    error_message: str | None = None,
    error_location: str | None = None,
    error_stack: str | None = None,
):
    """Write a structured JSON log line to stdout for Filebeat to capture."""
    entry: dict = {
        "@timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "level":         level,
        "service":       "coderz-webapp",
        "client_ip":     client_ip,
        "http_method":   method,
        "http_path":     path,
        "http_query":    query or None,
        "http_status":   status,
        "duration_ms":   duration_ms,
        "user_agent":    user_agent or None,
        "error_type":    error_type,
        "error_message": error_message,
        "error_location":error_location,
        "error_stack":   error_stack,
    }
    # Remove None values for clean output
    entry = {k: v for k, v in entry.items() if v is not None}
    print(json.dumps(entry), flush=True)

# ── Structured Logging + Metrics Middleware ───────────────────────────────────

@app.middleware("http")
async def request_middleware(request: Request, call_next):
    import re

    path      = request.url.path
    norm_path = re.sub(r"/\d+", "/{id}", path)
    query     = request.url.query
    method    = request.method
    client_ip = (
        request.headers.get("X-Real-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    user_agent = request.headers.get("User-Agent", "")

    # Skip health/metrics from logging
    skip_log = path in ("/metrics", "/api/health", "/health")

    ACTIVE_REQUESTS.inc()
    start      = time.time()
    status     = 500
    error_type = error_msg = error_loc = error_stack_str = None

    try:
        response  = await call_next(request)
        status    = response.status_code
        return response

    except Exception as exc:
        status         = 500
        error_type     = type(exc).__name__
        error_msg      = str(exc)
        tb             = traceback.extract_tb(exc.__traceback__)
        if tb:
            last        = tb[-1]
            error_loc   = f"{last.filename.split('/')[-1]}:{last.lineno} ({last.name})"
        error_stack_str = traceback.format_exc()
        ERROR_TOTAL.labels(endpoint=norm_path, error_type=error_type).inc()
        raise

    finally:
        duration_ms = int((time.time() - start) * 1000)

        REQUEST_TOTAL.labels(
            method=method, endpoint=norm_path, status_code=str(status)
        ).inc()
        REQUEST_DURATION.labels(method=method, endpoint=norm_path).observe(
            time.time() - start
        )
        ACTIVE_REQUESTS.dec()

        if not skip_log:
            level = "ERROR" if status >= 500 else ("WARN" if status >= 400 else "INFO")
            _emit_log(
                level       = level,
                method      = method,
                path        = path,
                query       = query,
                client_ip   = client_ip,
                status      = status,
                duration_ms = duration_ms,
                user_agent  = user_agent,
                error_type  = error_type,
                error_message  = error_msg,
                error_location = error_loc,
                error_stack    = error_stack_str,
            )

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    await simulate_db(5, 20, "RENDER")
    return """<!DOCTYPE html>
<html><head><title>Coderz Web API</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#0d1117;color:#e6edf3;margin:0;padding:40px;}
  .container{max-width:760px;margin:0 auto;}
  h1{color:#58a6ff;font-size:28px;margin-bottom:8px;}
  .badge{display:inline-block;background:#238636;color:#fff;
         padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:24px 0;}
  .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;}
  .card h3{color:#58a6ff;margin:0 0 8px;font-size:14px;text-transform:uppercase;
           letter-spacing:.05em;}
  .ep{padding:6px 0;border-bottom:1px solid #21262d;font-size:13px;font-family:monospace;}
  .ep:last-child{border:none;}
  .method{display:inline-block;width:46px;text-align:center;border-radius:4px;
          padding:2px 0;font-size:11px;font-weight:700;margin-right:8px;}
  .get{background:#0d419d;color:#79c0ff;}
  .post{background:#1a3c20;color:#56d364;}
  a{color:#58a6ff;text-decoration:none;}
  a:hover{text-decoration:underline;}
</style></head><body>
<div class="container">
  <h1>Coderz Web API</h1>
  <p style="color:#8b949e;margin-bottom:20px;">
    <span class="badge">● RUNNING</span>
    &nbsp; v1.0.0 &nbsp;·&nbsp; REST API · Prometheus · Redis Cache · ELK Logging
  </p>
  <div class="grid">
    <div class="card">
      <h3>API Endpoints</h3>
      <div class="ep"><span class="method get">GET</span><a href="/api/health">/api/health</a></div>
      <div class="ep"><span class="method get">GET</span><a href="/api/users">/api/users</a></div>
      <div class="ep"><span class="method get">GET</span>/api/users/{id}</div>
      <div class="ep"><span class="method get">GET</span><a href="/api/products">/api/products</a></div>
      <div class="ep"><span class="method post">POST</span>/api/orders</div>
      <div class="ep"><span class="method get">GET</span><a href="/metrics">/metrics</a></div>
    </div>
    <div class="card">
      <h3>Resources</h3>
      <div class="ep">👤 Users: 100 records</div>
      <div class="ep">📦 Products: 200 records</div>
      <div class="ep"><a href="/swagger">📖 Swagger UI</a></div>
      <div class="ep"><a href="/metrics">📊 Prometheus /metrics</a></div>
    </div>
  </div>
</div></body></html>"""


@app.get("/api/health")
async def health():
    r     = await get_redis()
    redis_ok = False
    if r:
        try:
            await r.ping()
            redis_ok = True
        except Exception:
            pass
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": time.time(),
        "uptime_seconds": time.time() - _start_time,
        "services": {"database": "connected", "cache": "connected" if redis_ok else "unavailable"}
    }


@app.get("/api/users")
async def get_users(page: int = 1, limit: int = 10):
    cache_key = f"webapp:users:{page}:{limit}"
    r = await get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return Response(content=cached, media_type="application/json",
                                headers={"X-Cache": "HIT"})
        except Exception:
            pass

    await simulate_db(10, 60, "SELECT")
    if random.random() < 0.02:
        ERROR_TOTAL.labels(endpoint="/api/users", error_type="DatabaseTimeout").inc()
        raise HTTPException(status_code=503, detail="Database connection timeout")

    limit = min(limit, 50)
    start = (page - 1) * limit
    result = {
        "data": USERS[start:start + limit],
        "pagination": {
            "page": page, "limit": limit,
            "total": len(USERS),
            "pages": (len(USERS) + limit - 1) // limit
        }
    }
    payload = json.dumps(result)
    if r:
        try:
            await r.setex(cache_key, 30, payload)
        except Exception:
            pass
    return Response(content=payload, media_type="application/json",
                    headers={"X-Cache": "MISS"})


@app.get("/api/users/{user_id}")
async def get_user(user_id: int):
    cache_key = f"webapp:user:{user_id}"
    r = await get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return Response(content=cached, media_type="application/json",
                                headers={"X-Cache": "HIT"})
        except Exception:
            pass

    await simulate_db(5, 30, "SELECT_BY_ID")
    user = next((u for u in USERS if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    payload = json.dumps(user)
    if r:
        try:
            await r.setex(cache_key, 120, payload)
        except Exception:
            pass
    return Response(content=payload, media_type="application/json",
                    headers={"X-Cache": "MISS"})


@app.get("/api/products")
async def get_products(
    page: int = 1, limit: int = 20,
    category: str = None, min_price: float = 0
):
    cache_key = f"webapp:products:{page}:{limit}:{category}:{min_price}"
    r = await get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return Response(content=cached, media_type="application/json",
                                headers={"X-Cache": "HIT"})
        except Exception:
            pass

    await simulate_db(15, 90, "SELECT_FILTERED")
    items = [
        p for p in PRODUCTS
        if p["price"] >= min_price
        and (category is None or p["category"].lower() == category.lower())
    ]
    limit  = min(limit, 100)
    start  = (page - 1) * limit
    result = {
        "data": items[start:start + limit],
        "pagination": {
            "page": page, "limit": limit,
            "total": len(items),
            "pages": (len(items) + limit - 1) // limit
        }
    }
    payload = json.dumps(result)
    if r:
        try:
            await r.setex(cache_key, 30, payload)
        except Exception:
            pass
    return Response(content=payload, media_type="application/json",
                    headers={"X-Cache": "MISS"})


@app.post("/api/orders", status_code=201)
async def create_order(request: Request):
    body = await request.body()
    REQUEST_SIZE.labels(endpoint="/api/orders").observe(len(body))
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}

    await simulate_db(20, 130, "INSERT")

    if random.random() < 0.05:
        ERROR_TOTAL.labels(endpoint="/api/orders", error_type="ValidationError").inc()
        raise HTTPException(status_code=422, detail="Invalid order: insufficient stock")

    return {
        "order_id": random.randint(100000, 999999),
        "status": "created",
        "items": data.get("items", []),
        "total": round(random.uniform(10.0, 2000.0), 2),
        "currency": "USD",
        "estimated_delivery": "3-5 business days"
    }


@app.get("/api/stats")
async def stats():
    cache_key = "webapp:stats"
    r = await get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return Response(content=cached, media_type="application/json",
                                headers={"X-Cache": "HIT"})
        except Exception:
            pass

    await simulate_db(5, 25, "AGGREGATE")
    result = {
        "users": {"total": len(USERS), "active": sum(1 for u in USERS if u["active"])},
        "products": {
            "total": len(PRODUCTS),
            "in_stock": sum(1 for p in PRODUCTS if p["stock"] > 0),
            "categories": list({p["category"] for p in PRODUCTS})
        }
    }
    payload = json.dumps(result)
    if r:
        try:
            await r.setex(cache_key, 60, payload)
        except Exception:
            pass
    return Response(content=payload, media_type="application/json",
                    headers={"X-Cache": "MISS"})


# ── Startup ───────────────────────────────────────────────────────────────────

_start_time = time.time()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
