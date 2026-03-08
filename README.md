# Coderz Stack

Full-stack observability and infrastructure platform running on Docker Compose.

**Server:** `109.199.120.120`
**Docs:** http://109.199.120.120:3333

---

## Services

| Service | Port | Purpose |
|---|---|---|
| Nginx | 80 | Landing page |
| Grafana | 3000 | Dashboards & alerting |
| Prometheus | 9090 | Metrics storage |
| Loki | 3100 | Log aggregation |
| OTel Collector | 4317 / 4318 | Distributed tracing pipeline |
| Elasticsearch | 9200 | Trace & log storage |
| Kibana | 5601 | Log & trace exploration |
| Logstash | 5044 | Log processing |
| Prefect | 4200 | Workflow orchestration |
| .NET API | 5050 | REST API (ASP.NET Core 8) |
| Web API | 8888 | Node.js web API |
| PostgreSQL | 5433 | Relational DB (CoderAPI) |
| pgAdmin | 5080 | DB admin UI |
| k6 Runner | 9000 | Load testing UI |
| Node Exporter | 9100 | Host metrics |
| cAdvisor | 8080 | Container metrics |

---

## OpenTelemetry

The stack uses OpenTelemetry for distributed tracing, runtime metrics, and structured logs from the .NET API.

### Architecture

```
.NET API  ──OTLP/gRPC──►  OTel Collector
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
          Elasticsearch   Elasticsearch  Prometheus
          (traces index)  (logs index)  (remote_write)
                │              │              │
              Kibana          Kibana        Grafana
```

### What is collected

| Signal | What | Where to view |
|---|---|---|
| **Traces** | Per-request spans: HTTP handler, DB queries, cache lookups | Kibana → OTel Traces data view |
| **Metrics** | HTTP latency, GC, thread pool, heap | Grafana → .NET API Full Stack |
| **Logs** | Structured logs with TraceId/SpanId attached | Kibana → OTel Logs data view |

### Why OTel?

- **Vendor-neutral** — swap backends by changing `configs/otel-collector/config.yml`, not application code
- **Trace correlation** — every log line carries a `TraceId` that links to the full request trace
- **Single SDK** — one `AddOpenTelemetry()` call in `Program.cs` instruments traces + metrics + logs
- **Industry standard** — same instrumentation works with Jaeger, Tempo, Datadog, Honeycomb, etc.

### Collector config

`configs/otel-collector/config.yml` — receives OTLP on ports 4317 (gRPC) and 4318 (HTTP), batches signals, and exports to Elasticsearch and Prometheus.

### .NET API instrumentation

`configs/dotnet-api/Program.cs` — instruments via:
- `AddAspNetCoreInstrumentation()` — HTTP request spans
- `AddEntityFrameworkCoreInstrumentation()` — PostgreSQL query spans (with SQL text)
- `AddHttpClientInstrumentation()` — outbound HTTP spans
- `AddRuntimeInstrumentation()` — GC, thread pool, heap metrics
- `AddOtlpExporter()` — sends all signals to `http://otel-collector:4317`

Full documentation: http://109.199.120.120:3333/monitoring/opentelemetry

---

## Quick Start

```bash
cd /opt/coderz
docker compose up -d
```

Check all services are healthy:

```bash
docker compose ps
```

---

## Config Files

```
configs/
├── otel-collector/config.yml     # OTel Collector pipelines
├── dotnet-api/Program.cs         # .NET API OTel SDK setup
├── prometheus/prometheus.yml     # Scrape targets + remote_write
├── grafana/provisioning/         # Dashboards + datasources + alerting
├── loki/loki-config.yml          # Loki storage config
├── promtail/promtail-config.yml  # Log scraping
├── kibana/                       # Kibana data views + dashboards
├── logstash/                     # Logstash pipelines
├── prefect/flows/                # Prefect workflow definitions
├── k6-runner/                    # Load test scenarios
└── nginx/                        # Landing page
```

---

## Kubernetes (k3s)

k3s is installed and runs a sample `coderz-web` deployment (2 replicas) in the `coderz` namespace. Manifests are in `k8s/`.

```bash
kubectl get pods -A
```
