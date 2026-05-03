# AI Kubernetes Observability Backend

Production-hardened FastAPI backend for AI-driven Kubernetes pod observability. The backend collects Prometheus metrics, queues them in Redis, detects anomalies with deterministic rolling z-scores, correlates events with NetworkX, stores data in PostgreSQL, and uses Gemini for reasoning and explanations.

Gemini is **not** required for availability — if the API key is missing or Gemini is unreachable, the backend continues running and returns deterministic fallback insights.

---

## Architecture

```text
Kubernetes
  -> Prometheus (metrics scraping)
  -> Metrics Collector (polls every N seconds)
  -> Redis Queue (buffering)
  -> Detection Agents (rolling z-score anomaly detection)
  -> Correlation Engine (NetworkX dependency graph)
  -> Gemini Reasoning Engine (root-cause analysis)
  -> FastAPI API (serves results)
  -> Dashboard
```

## What The Services Do

| Component | Description |
|---|---|
| **Metrics Collector** | Polls Prometheus every `METRICS_POLL_INTERVAL_SECONDS` (default: 10s) for CPU, memory, disk I/O, network throughput, PVC usage, and pod restart counts |
| **Detection Agents** | Maintain rolling windows of `ROLLING_WINDOW_SIZE` values (default: 50). An anomaly is created when `abs(z_score) > ANOMALY_ZSCORE_THRESHOLD` (default: 2.0) |
| **Correlation Engine** | Creates dependency edges when events occur within `CORRELATION_WINDOW_SECONDS` (default: 5s) and correlation exceeds `CORRELATION_THRESHOLD` (default: 0.8) |
| **Gemini Reasoning** | Uses `GEMINI_MODEL` (default: `gemini-2.5-flash`) for root-cause explanations and recommendations |
| **PostgreSQL** | Stores metrics, anomalies, and AI-generated insights. Auto-purges data older than `RETENTION_DAYS` (default: 7) |
| **Redis** | In-memory event queue between collector and processor |

---

## Project Structure

```text
.
├── app/
│   ├── main.py                 # FastAPI app, lifespan, CORS middleware
│   ├── api/
│   │   └── routes.py           # API endpoints (public + protected)
│   ├── agents/
│   │   └── detection.py        # Z-score anomaly detection agents
│   ├── core/
│   │   ├── auth.py             # API key authentication dependency
│   │   ├── config.py           # Pydantic settings (reads from .env)
│   │   ├── logging.py          # Structured JSON logging
│   │   ├── models.py           # Data models (MetricPoint, AnomalyEvent, etc.)
│   │   └── state.py            # Runtime state container
│   └── services/
│       ├── collector.py        # Prometheus metrics collector
│       ├── correlation.py      # NetworkX correlation engine
│       ├── gemini.py           # Gemini reasoning engine
│       ├── processor.py        # Event processor pipeline
│       ├── queue.py            # Redis metric queue
│       └── storage.py          # PostgreSQL storage + retention
├── k8s/                        # Kubernetes manifests (production)
│   ├── namespace.yaml
│   ├── pvc.yaml
│   ├── deployment.yaml
│   ├── service.yaml
│   └── secret.example.yaml
├── prometheus/
│   └── prometheus.yml          # Prometheus scrape config
├── tests/
│   └── test_detection.py       # Unit tests
├── docker-compose.yml          # Local dev dependencies
├── Dockerfile                  # Multi-stage production image
├── requirements.txt
├── .env.example                # All configurable environment variables
└── .gitignore
```

---

## Quick Start (Local Development)

### 1. Clone and Enter the Project

```bash
cd /path/to/ABBPROJECT
```

### 2. Create and Activate Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Or use the venv binaries directly without activation:

```bash
.venv/bin/python
.venv/bin/pip
```

### 3. Install Dependencies

```bash
.venv/bin/pip install -r requirements.txt
```

Verify the app imports correctly:

```bash
.venv/bin/python -c "from app.main import app; print(app.title)"
# Expected: AI Kubernetes Observability Backend
```

### 4. Start Local Dependencies

Redis, PostgreSQL, and Prometheus are managed via Docker Compose:

```bash
docker compose up -d
```

Check all services are healthy:

```bash
docker compose ps
```

Expected:

```text
redis       Up (healthy)
postgres    Up (healthy)
prometheus  Up (healthy)
```

> **Note:** PostgreSQL is mapped to host port `55432` (not `5432`) to avoid conflicts with any system PostgreSQL installation.

### 5. Configure Environment

Copy the example and fill in your values:

```bash
cp .env.example .env
```

Edit `.env` and set your Gemini API key:

```bash
nano .env
```

The `.env.example` contains every configurable setting, organized by category:

```env
# ── Infrastructure ──────────────────────────────────────────────────────
PROMETHEUS_URL=http://localhost:9090
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql://observability:observability@localhost:55432/observability

# ── Gemini AI ───────────────────────────────────────────────────────────
GEMINI_API_KEY=replace-with-your-gemini-api-key
GEMINI_MODEL=gemini-2.5-flash

# ── Collector / Detection Tuning ────────────────────────────────────────
METRICS_POLL_INTERVAL_SECONDS=10
ROLLING_WINDOW_SIZE=50
ANOMALY_ZSCORE_THRESHOLD=2.0

# ── Correlation ─────────────────────────────────────────────────────────
CORRELATION_WINDOW_SECONDS=5
CORRELATION_THRESHOLD=0.8

# ── Storage ─────────────────────────────────────────────────────────────
GRAPH_PATH=./dependency_graph.json
RETENTION_DAYS=7

# ── API Server ──────────────────────────────────────────────────────────
API_HOST=0.0.0.0
API_PORT=8000
API_KEY=
CORS_ORIGINS=["*"]
```

If `GEMINI_API_KEY` is not set, the `/health` endpoint will show `"gemini": "fallback"` — this is expected. The backend still runs and returns deterministic fallback insights.

### 6. Start the Backend

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Expected logs:

```text
Application startup complete.
Uvicorn running on http://0.0.0.0:8000
database_connected
metric_collected
```

Keep this terminal open. Use another terminal for API calls.

### 7. Run Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

---

## API Endpoints

### Public (no auth required)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check — shows status of database, queue, collector, and Gemini |
| `GET` | `/system/metrics` | Internal system metrics (queue size, latency, error rate) |

### Protected (requires `X-API-Key` header when `API_KEY` is set)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/metrics/recent` | Last 100 collected Prometheus metrics |
| `GET` | `/anomalies` | Detected anomaly events |
| `GET` | `/dependencies` | Dependency graph (NetworkX export) |
| `GET` | `/insights` | AI-generated root-cause insights |
| `GET` | `/gemini/test` | Test Gemini connectivity |

### API Key Authentication

When `API_KEY` is set in `.env`, all protected endpoints require the `X-API-Key` header:

```bash
# Without API key configured — works directly
curl http://localhost:8000/metrics/recent

# With API key configured
curl -H "X-API-Key: your-api-key-here" http://localhost:8000/metrics/recent
```

If `API_KEY` is empty or unset, authentication is **skipped** — this allows easy local development without extra setup.

---

## Environment Variables Reference

All settings are managed through `app/core/config.py` using Pydantic Settings. They can be set via `.env` file or environment variables (env vars take precedence).

| Variable | Default | Description |
|---|---|---|
| `PROMETHEUS_URL` | `http://prometheus:9090` | Prometheus server URL |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |
| `DATABASE_URL` | `postgresql://...@postgres:5432/...` | PostgreSQL connection string |
| `GEMINI_API_KEY` | `None` | Google Gemini API key (optional) |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model to use |
| `METRICS_POLL_INTERVAL_SECONDS` | `10` | How often to poll Prometheus |
| `ROLLING_WINDOW_SIZE` | `50` | Number of values in the detection rolling window |
| `ANOMALY_ZSCORE_THRESHOLD` | `2.0` | Z-score threshold to trigger an anomaly |
| `CORRELATION_WINDOW_SECONDS` | `5` | Time window for event correlation |
| `CORRELATION_THRESHOLD` | `0.8` | Minimum correlation to create a dependency edge |
| `GRAPH_PATH` | `/data/dependency_graph.json` | Path to persist the dependency graph |
| `RETENTION_DAYS` | `7` | Days to keep data before automatic cleanup |
| `API_HOST` | `0.0.0.0` | API server bind address |
| `API_PORT` | `8000` | API server port |
| `API_KEY` | `None` | API key for protected endpoints (disabled if empty) |
| `CORS_ORIGINS` | `["*"]` | Allowed CORS origins |

> **Local override:** For local development, `GRAPH_PATH` should be `./dependency_graph.json` (set in `.env`). The `/data/` default is intended for the Kubernetes PVC mount.

---

## Data Retention

The backend automatically purges old data every hour. The `RETENTION_DAYS` setting (default: 7) controls the cutoff. Three tables are cleaned:

- `metrics` — raw Prometheus metric rows
- `anomalies` — detected anomaly events
- `insights` — AI-generated root-cause insights

---

## CORS Configuration

CORS middleware is enabled in `app/main.py`. The allowed origins are controlled by the `CORS_ORIGINS` environment variable.

For local development (allow all):

```env
CORS_ORIGINS=["*"]
```

For production (restrict to your frontend domain):

```env
CORS_ORIGINS=["https://dashboard.example.com"]
```

---

## Docker

### Build the Image

The Dockerfile uses a **multi-stage build** with a pinned base image (`python:3.12.8-slim`), runs as a **non-root user** (`appuser`), and starts with **gunicorn** (4 Uvicorn workers):

```bash
docker build -t ai-observability-backend:latest .
```

### Run with Docker

```bash
docker run --rm -p 8000:8000 --env-file .env ai-observability-backend:latest
```

> **Note:** When running with `--env-file .env`, make sure the URLs point to reachable hosts. If the container needs to reach docker-compose services, use `--network` or adjust the URLs.

---

## Kubernetes Deployment

Production-ready manifests are in `k8s/`. These are **not used for local development** — they are templates for deploying to a Kubernetes cluster.

### Manifests

| File | Purpose |
|---|---|
| `namespace.yaml` | Creates the `ai-observability` namespace |
| `pvc.yaml` | 100Mi PersistentVolumeClaim for dependency graph persistence |
| `secret.example.yaml` | Template for secrets (database URL, Gemini key, API key) |
| `deployment.yaml` | Hardened deployment with security contexts, probes, and resource limits |
| `service.yaml` | ClusterIP service on port 8000 |

### Security Features (Deployment)

- **Pod-level:** `runAsNonRoot`, UID/GID `1000`
- **Container-level:** `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, drops all Linux capabilities
- **Probes:** Startup (5s initial, 10 attempts), readiness (10s interval), liveness (30s interval)
- **Resources:** 250m–1 CPU, 256Mi–1Gi memory
- **Secrets:** `DATABASE_URL`, `GEMINI_API_KEY`, `API_KEY` injected from K8s Secret

### Deploy to a Cluster

```bash
# 1. Create namespace
kubectl apply -f k8s/namespace.yaml

# 2. Create your real secret (copy from example, fill in real values)
cp k8s/secret.example.yaml k8s/secret.yaml
nano k8s/secret.yaml
kubectl apply -f k8s/secret.yaml

# 3. Create PVC for graph persistence
kubectl apply -f k8s/pvc.yaml

# 4. Deploy
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# 5. Verify
kubectl -n ai-observability get pods
kubectl -n ai-observability logs -f deployment/ai-observability-backend
```

> **Important:** Never commit `k8s/secret.yaml` — it is listed in `.gitignore`. Only `secret.example.yaml` is tracked.

---

## Connecting to Kubernetes Prometheus

If `/metrics/recent` returns `[]`, Prometheus has no Kubernetes pod metrics.

Find your cluster's Prometheus service:

```bash
kubectl get svc -A | grep -i prometheus
```

Port-forward to make it accessible locally:

```bash
# For prometheus-server
kubectl -n monitoring port-forward svc/prometheus-server 9090:80

# For kube-prometheus-stack
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

Verify metrics are available:

```bash
curl -s "http://localhost:9090/api/v1/query?query=container_cpu_usage_seconds_total"
```

If `"result"` contains data, the backend will start collecting and processing metrics.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `uvicorn` not found | Use `.venv/bin/uvicorn` or activate the venv first |
| Port 8000 already in use | Check with `ss -ltnp \| grep 8000`, use `--port 8001` |
| Database connection failed | Ensure `docker compose up -d postgres` is running and `.env` uses port `55432` |
| `/metrics/recent` returns `[]` | Prometheus has no K8s metrics — see [Connecting to Kubernetes Prometheus](#connecting-to-kubernetes-prometheus) |
| `"gemini": "fallback"` in `/health` | `GEMINI_API_KEY` not set — backend works without it, returning deterministic fallback insights |
| Protected endpoint returns 401 | Include `X-API-Key` header or unset `API_KEY` in `.env` for local dev |

---

## Stopping Services

Stop the backend: `Ctrl+C` in the uvicorn terminal.

Stop Docker dependencies:

```bash
# Stop containers (keep data)
docker compose down

# Stop and delete PostgreSQL data volume
docker compose down -v
```
