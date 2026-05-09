# AI Kubernetes Observability Backend

Production-hardened FastAPI backend for AI-driven Kubernetes pod observability. The backend collects Prometheus metrics, queues them in Redis, detects anomalies with deterministic rolling z-scores, correlates events with NetworkX, stores data in PostgreSQL, and uses Gemini for reasoning and explanations.

Gemini is **not** required for availability — if the API key is missing or Gemini is unreachable, the backend continues running and returns deterministic fallback insights.

---

## Architecture

The system is designed as a high-performance, asynchronous pipeline split between **Telemetry Collection** and **Intelligence Serving**.

### System Overview

```mermaid
graph TD
    subgraph "Kubernetes Cluster"
        K8s[K8s Pods/Nodes] -->|Scrape| Prom[Prometheus Stack]
    end

    subgraph "Observability Worker"
        Collector[Metrics Collector] -->|Poll| Prom
        Collector -->|Push| Redis[Redis Stream]
        Redis -->|Consume| Detection[Detection Agents]
        Detection -->|Z-Score| Anomaly[Anomaly Store]
        Anomaly -->|Event| Correlation[Correlation Engine]
        Correlation -->|Edge| Graph[Dependency Graph]
    end

    subgraph "Gemini AI Core"
        InsightWorker[Insight Worker] -->|Batch| Gemini[Gemini API]
        ClusterAnalyzer[Cluster Analyzer] -->|Correlation| Gemini
    end

    subgraph "API & Dashboard"
        API[FastAPI Server] -->|Query| DB[(PostgreSQL)]
        API -->|Read| Graph
        API -->|Trigger| ClusterAnalyzer
        API -->|Auth| User[Dashboard / Client]
    end

    Detection -->|Store| DB
    InsightWorker -->|Store| DB
    InsightWorker -->|Poll| Anomaly
```

### Logical Data Flow

```text
Telemetry [K8s/Prometheus]
   │
   ▼
[Metrics Collector] ── (Poll 10s) ──► [Redis Stream]
                                         │
                                         ▼
[Detection Agents] ── (Z-Score) ──► [Anomaly Events]
                                         │
               ┌─────────────────────────┴─────────────────────────┐
               ▼                                                   ▼
[Per-Pod Analysis]                                      [Cluster-Wide Analysis]
  (Insight Worker)                                         (Cluster Analyzer)
       │                                                       │
       ├─► [Gemini AI] ◄───────────────────────────────────────┘
       │
       ▼
[PostgreSQL Store] ◄──────────────── [FastAPI API] ◄──────── [User/Dashboard]
```

## What The Services Do

| Component | Description |
|---|---|
| **Metrics Collector** | Polls Prometheus every `METRICS_POLL_INTERVAL_SECONDS` (default: 10s) for CPU, memory, disk I/O, network throughput, PVC usage, and pod restart counts |
| **Detection Agents** | Maintain rolling windows of `ROLLING_WINDOW_SIZE` values (default: 50). An anomaly is created when `abs(z_score) > ANOMALY_ZSCORE_THRESHOLD` (default: 2.0) |
| **Correlation Engine** | Creates dependency edges when events occur within `CORRELATION_WINDOW_SECONDS` (default: 5s) and correlation exceeds `CORRELATION_THRESHOLD` (default: 0.8) |
| **Gemini Reasoning** | Uses `GEMINI_MODEL` (default: `gemini-3-flash`) for root-cause explanations and recommendations. Insight requests are batched, cached, rate-limited, and only critical anomalies are sent to Gemini by default |
| **Cluster Analyzer** | Aggregates anomalies, metrics, and dependency edges across the entire cluster and sends them to Gemini for cross-pod, cross-namespace incident correlation. Returns the top 5 most critical incidents. Falls back to deterministic ranking when Gemini is unavailable |
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
│       ├── cluster.py         # Cluster-wide SRE incident intelligence
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

> **Note:** PostgreSQL is mapped to host port `55432` and Redis is mapped to `56379` to avoid conflicts with system-level installations.

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
GEMINI_MODEL=gemini-3-flash
GEMINI_REQUESTS_PER_MINUTE=2
INSIGHT_BATCH_WINDOW_SECONDS=30
INSIGHT_BATCH_SIZE=10
AI_MIN_SEVERITY=critical
INSIGHT_CACHE_SIZE=256

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

### 6. Start the Backend (API & Worker)

The backend architecture is split into two distinct processes: an API service and a background Worker. **Both must be running simultaneously.**

#### Option A: Using Docker Compose (Recommended)

The easiest way to run the entire stack (Redis, Postgres, Prometheus, API, and Worker) is via Docker Compose:

```bash
# Start all services in the background
docker compose up -d
```

To view logs for the backend services:

```bash
docker compose logs -f api worker
```

#### Option B: Running Locally (Virtual Environment)

If you prefer to run the Python code locally on your host (while keeping Redis/Postgres/Prometheus in Docker), you must open **two separate terminal windows**.

**Terminal 1 — Start the API Server:**
```bash
# Activates the API process serving HTTP traffic
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```
*Expected logs: `api_started` and `Uvicorn running on http://0.0.0.0:8000`*

**Terminal 2 — Start the Background Worker:**
```bash
# Activates the background collector, anomaly detector, and AI processor
.venv/bin/python -m app.worker
```
*Expected logs: `worker_started` and recurring `anomalies_detected` / `insight_generated` events*

---

### 7. Stop the Backend

**If using Docker Compose:**
Stop everything gracefully (preserves database data in volumes):
```bash
docker compose down
```

**If running locally:**
Press `Ctrl+C` in both the API and Worker terminal windows. The processes are configured to intercept the shutdown signal, finish their current tasks, close database/queue connections, and exit cleanly.

---

### 8. Run Tests


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
| `GET` | `/cluster/incidents` | Cluster-wide SRE incident correlation report (top 5 critical incidents) |
| `GET` | `/gemini/test` | Test Gemini connectivity |

### Cluster-Wide Incident Intelligence

The `/cluster/incidents` endpoint is the platform's **highest-level intelligence layer**. Unlike `/insights` (which analyzes individual pods), this endpoint performs **cluster-wide incident correlation** — aggregating all active anomalies, metrics, and dependency relationships across every namespace to identify the most critical operational incidents.

#### How It Works

```mermaid
sequenceDiagram
    participant U as User/Client
    participant A as ClusterAnalyzer
    participant S as StorageEngine
    participant C as CorrelationEngine
    participant G as Gemini Reasoning Engine

    U->>A: GET /cluster/incidents
    A->>A: Check Cache
    alt Cache Hit
        A-->>U: Return Cached Report
    else Cache Miss
        A->>S: query_anomalies(last_1h)
        S-->>A: [Anomalies]
        A->>S: query_metrics(last_1h)
        S-->>A: [Metrics]
        A->>C: export_json()
        C-->>A: [Dependency Graph]
        A->>A: Build Structured Snapshot
        
        alt Gemini Configured
            A->>G: call_gemini(snapshot)
            G-->>A: [AI Correlation Report]
        else Fallback Mode
            A->>A: deterministic_rank(snapshot)
        end
        
        A->>A: Update Cache (60s)
        A-->>U: Return Incident Report
    end
```

#### Data Sources

The endpoint aggregates data from three sources into a single structured telemetry snapshot:

| Source | Data | Window |
|---|---|---|
| **Anomalies** | All detected anomalies (CPU, memory, disk, network, PVC, restarts) with severity and z-score | Last 1 hour |
| **Metrics** | Latest metric values per pod per metric type | Last 1 hour |
| **Dependencies** | Pod-to-pod correlation edges from the NetworkX dependency graph | All time (persisted) |

#### Gemini AI Integration

This endpoint **uses the Gemini API** (same key and rate limiter as `/insights`). The analysis follows two distinct paths:

**Path 1 — AI-Powered Analysis (when `GEMINI_API_KEY` is set):**
- Sends the full cluster snapshot to Gemini with a production-grade SRE prompt
- Gemini performs cross-pod, cross-namespace causal reasoning
- Identifies cascading failures, shared root causes, and dependency chains
- Returns structured JSON with `"fallback": false`
- Retries up to 3 times with exponential backoff on failure
- If all retries fail, falls back to Path 2

**Path 2 — Deterministic Fallback (no API key or Gemini failure):**
- Ranks anomalies by a composite score: `severity_rank × 100 + z_score × 10 + anomaly_count`
- Groups by pod and takes the top 5 highest-scoring pods
- Returns structured JSON with `"fallback": true` and `"confidence": "Low"`
- No external API calls — purely deterministic

#### Confidence Scoring

| Level | Meaning |
|---|---|
| **High** | Clear causal chain supported by strong telemetry signals |
| **Medium** | Likely root cause inferred, but alternative causes remain plausible |
| **Low** | Insufficient or conflicting evidence (always used in fallback mode) |

#### Caching

Responses are cached for **60 seconds** to prevent Gemini rate-limiting on rapid dashboard refreshes. The first call within the window hits Gemini; subsequent calls return the cached report instantly.

#### Usage

```bash
# Via Kubernetes port-forward (Postman-compatible)
curl -s -H "X-API-Key: your-key" http://localhost:8001/cluster/incidents | jq

# Via local Docker Compose
curl -s http://localhost:8000/cluster/incidents | jq
```

#### Response Schema

```json
{
  "timestamp": "2026-05-09T12:00:00Z",
  "cluster_summary": "Short overall cluster health summary",
  "critical_incidents": [
    {
      "rank": 1,
      "incident_title": "Short incident title",
      "affected_services": ["service-a", "service-b"],
      "affected_pods": ["namespace/pod-name"],
      "root_cause": "Most probable operational root cause",
      "impact": "Operational impact description",
      "confidence": "High | Medium | Low",
      "recommendation": "Specific actionable mitigation step"
    }
  ],
  "fallback": false
}
```

| Field | Type | Description |
|---|---|---|
| `timestamp` | ISO 8601 | When the report was generated |
| `cluster_summary` | string | One-line cluster health assessment |
| `critical_incidents` | array | 1–5 incidents ranked by operational risk |
| `critical_incidents[].rank` | integer | Priority rank (1 = most critical) |
| `critical_incidents[].affected_services` | string[] | Service names involved |
| `critical_incidents[].affected_pods` | string[] | Full `namespace/pod` identifiers |
| `critical_incidents[].root_cause` | string | Most probable root cause |
| `critical_incidents[].impact` | string | Operational impact description |
| `critical_incidents[].confidence` | string | Exactly `High`, `Medium`, or `Low` |
| `critical_incidents[].recommendation` | string | Actionable mitigation step |
| `fallback` | boolean | `true` if Gemini was unavailable |

#### Example: AI-Powered Response

```json
{
  "timestamp": "2026-05-09T12:00:00Z",
  "cluster_summary": "Critical CPU saturation on payment-service causing cascading latency across 3 dependent services",
  "critical_incidents": [
    {
      "rank": 1,
      "incident_title": "CPU exhaustion on payment-service",
      "affected_services": ["payment-service", "checkout-service"],
      "affected_pods": ["default/payment-service-abc123"],
      "root_cause": "Unbounded thread pool causing CPU starvation under load",
      "impact": "200ms SLO breach for payment and checkout flows",
      "confidence": "High",
      "recommendation": "Set CPU limits to 500m, add HPA with 70% target, and investigate the thread pool configuration"
    }
  ],
  "fallback": false
}
```

#### Example: Deterministic Fallback Response

```json
{
  "timestamp": "2026-05-09T12:00:00Z",
  "cluster_summary": "Deterministic analysis: 8 anomalies across 3 pods in the last hour. AI correlation unavailable.",
  "critical_incidents": [
    {
      "rank": 1,
      "incident_title": "cpu, memory anomaly on default/payment-service",
      "affected_services": ["payment-service"],
      "affected_pods": ["default/payment-service"],
      "root_cause": "Deterministic detection found 5 anomalies (cpu, memory) with severities: critical, high. AI reasoning unavailable: Gemini API key is not configured",
      "impact": "Pod payment-service in namespace default exhibiting abnormal cpu, memory behavior.",
      "confidence": "Low",
      "recommendation": "Inspect pod resource limits, recent restarts, node pressure, and correlated dependency edges."
    }
  ],
  "fallback": true
}
```

### Gemini API Key Rotation & Quota Management

To maximize uptime and bypass free-tier rate limits, the system supports **automatic API key rotation** and **intelligent retry logic**.

#### Key Rotation
- **Multi-Key Support**: Provide multiple Gemini API keys in your `.env` or Kubernetes secrets as a comma-separated list.
  ```env
  GEMINI_API_KEY="key1,key2,key3"
  ```
- **Round-Robin Execution**: The system cycles through available keys for every request (per-pod insights and cluster-wide analysis).
- **Graceful Fallthrough**: If one key hits a `429 (Too Many Requests)` or `Quota Exhausted` error, the system immediately rotates to the next key and retries the request.

#### Quota Tuning
| Variable | Default | Description |
|---|---|---|
| `GEMINI_REQUESTS_PER_MINUTE` | `2` | Global throttle for Gemini requests. If you have 3 keys, you can safely set this to `6`. |
| `AI_MIN_SEVERITY` | `critical` | Only pods with anomalies at this level or higher trigger background AI analysis. |
| `GEMINI_RETRY_ATTEMPTS` | `3` | Number of times to retry a failed AI request (each retry rotates the key). |

---

### Production Resiliency & Infrastructure

The following hardening measures have been implemented to ensure stability in production environments:

#### 1. Redis Port Conflict Resolution
- **Host Conflict**: If a system Redis is running on port `6379` on the host, Docker Compose is configured to map the container Redis to `56379`.
- **K8s Connectivity**: In Minikube/K8s environments, the deployment uses `host.minikube.internal:56379` to reach the Docker-managed Redis instance, avoiding conflicts with system services.

#### 2. Worker Startup Resiliency
- **Race Condition Handling**: During container startup, DNS resolution or Redis connectivity might be momentarily unavailable.
- **Exponential Backoff**: The worker's `ensure_group` logic includes a 5-retry exponential backoff, allowing it to survive transient infrastructure failures without crashing.

#### 3. State Management & Persistence
- **State Recovery**: The `RuntimeState` tracks Gemini health (`gemini_ok`) and error counts in memory.
- **Dependency Graph Persistence**: The NetworkX pod dependency graph is persisted to `/data/dependency_graph.json` to survive pod restarts.

---

### API Key Authentication

The `API_KEY` setting protects your backend from unauthorized access. 

To generate a secure, random API key, you can run this Python command:
```bash
python -c "import secrets; print(secrets.token_hex(16))"
# Example output: 556eaf3e2c550cbf4896941a9xxxxxxxx
```

Copy the output and place it in your `.env` file:
```env
API_KEY=556eaf3e2c550cbf4896941a9xxxxxxxx
```

**Important:** If you change the `.env` file while Docker is running, you must restart the containers for the new key to take effect:
```bash
docker compose down && docker compose up -d
```

When `API_KEY` is set, all protected endpoints require the `X-API-Key` header:

```bash
# With API key configured
curl -H "X-API-Key: your-api-key-here" http://localhost:8000/metrics/recent
```

If `API_KEY` is empty or unset (`API_KEY=`), authentication is **skipped** — this allows easy local development without extra setup.

---

## Environment Variables Reference

All settings are managed through `app/core/config.py` using Pydantic Settings. They can be set via `.env` file or environment variables (env vars take precedence).

| Variable | Default | Description |
|---|---|---|
| `PROMETHEUS_URL` | `http://prometheus:9090` | Prometheus server URL |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |
| `DATABASE_URL` | `postgresql://...@postgres:5432/...` | PostgreSQL connection string |
| `GEMINI_API_KEY` | `None` | Google Gemini API key (optional) |
| `GEMINI_MODEL` | `gemini-3-flash` | Gemini model to use |
| `GEMINI_REQUESTS_PER_MINUTE` | `2` | Maximum Gemini requests per worker process per minute |
| `INSIGHT_BATCH_WINDOW_SECONDS` | `30.0` | Time window used to collect anomaly insight requests before analysis |
| `INSIGHT_BATCH_SIZE` | `10` | Maximum insight requests to merge into one processing batch |
| `AI_MIN_SEVERITY` | `critical` | Minimum anomaly severity required before calling Gemini |
| `INSIGHT_CACHE_SIZE` | `256` | Number of repeated anomaly signatures cached in memory |
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
# 1. Create the namespace first
kubectl apply -f k8s/namespace.yaml

# 2. Create the ServiceAccount
kubectl apply -f k8s/serviceaccount.yaml

# 3. Create your real secret (copy from example, fill in real values)
cp k8s/secret.example.yaml k8s/secret.yaml
nano k8s/secret.yaml
kubectl apply -f k8s/secret.yaml

# 4. Create PVC for graph persistence
kubectl apply -f k8s/pvc.yaml

# 5. Deploy the API and Worker pods
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/worker-deployment.yaml

# 6. Apply Networking and Scaling rules
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/networkpolicy.yaml
kubectl apply -f k8s/hpa.yaml
kubectl apply -f k8s/pdb.yaml

kubectl -n ai-observability get pods,svc,hpa
```

### Accessing & Testing in Kubernetes

Since the services are running inside the cluster on a `ClusterIP` service, you must port-forward to access the API locally:

```bash
# 1. Port-forward the API (Backend)
kubectl -n ai-observability port-forward svc/ai-observability-backend 8001:8000

# 2. Test the connection (Health Check)
curl -s http://localhost:8001/health | jq

# 3. Test Protected Endpoints (using your API_KEY)
curl -s -H "X-API-Key: YOUR_API_KEY" http://localhost:8001/cluster/incidents | jq
```

> **Postman:** The provided collection is pre-configured to use `http://localhost:8001`. Ensure the port-forward is active before sending requests.

> **Important:** Never commit `k8s/secret.yaml` — it is listed in `.gitignore`. Only `secret.example.yaml` is tracked.

---

## Generating Real Data (Kubernetes Prometheus)

If `/metrics/recent` returns `[]`, it means your Prometheus instance is not connected to a Kubernetes cluster and has no pod metrics to scrape.

To get real Kubernetes metrics, install the industry-standard **kube-prometheus-stack** into your cluster using Helm:

```bash
# 1. Add the official Prometheus Helm repository
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# 2. Install the Prometheus stack into your cluster
helm install prometheus prometheus-community/kube-prometheus-stack -n monitoring --create-namespace
```

Once installed, ensure your `PROMETHEUS_URL` in `k8s/deployment.yaml` and `k8s/worker-deployment.yaml` points to the new in-cluster Prometheus:
`http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090`

---

## Testing with Postman

We provide an imported Postman Collection (`ai_observability_postman_collection.json`) in the root directory for easy testing.

1. Port-forward the Kubernetes API to your local machine:
   ```bash
   kubectl -n ai-observability port-forward svc/ai-observability-backend 8001:8000
   ```
2. Import `ai_observability_postman_collection.json` into Postman.
3. The collection is pre-configured to point to `http://localhost:8001` and injects your `X-API-Key` automatically. Simply hit **Send** on any endpoint.

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

## End-to-End Walkthrough: Seeing AI Insights in Action

Want to see the entire pipeline working from scratch? Follow these exact steps to simulate a pod failure, trigger an anomaly, and generate a Gemini AI insight.

### Step 1: Start the Stack
Make sure you have configured your `GEMINI_API_KEY` in your `.env` file. Then, start everything via Docker Compose:
```bash
docker compose up -d
```
Check that all containers (redis, postgres, prometheus, api, worker) are running:
```bash
docker compose ps
```

### Step 2: Observe Baseline Metrics
Wait about 30 seconds for the `worker` to collect a few data points and establish a baseline in the rolling window.
Open another terminal and verify metrics are flowing:
```bash
# Note: If API_KEY is set in .env, add: -H "X-API-Key: your-key"
curl -s http://localhost:8000/metrics/recent
```
*(You should see an array of JSON objects representing CPU/Memory/PVC usage).*

### Step 3: Simulate an Anomaly
By default, the backend monitors Prometheus data. Since you are running Prometheus locally without a real Kubernetes cluster attached, we can force a metric directly into the Redis queue to trigger an anomaly.

Run this command to push a massively spiked CPU metric directly into the Redis stream:
```bash
docker exec -it $(docker compose ps -q redis) redis-cli XADD metrics "*" data '{"pod_name": "payment-service", "namespace": "default", "metric_type": "cpu", "metric_value": 950.5, "timestamp": "2026-05-03T12:00:00Z"}'
```

### Step 4: Check Detected Anomalies
The `worker` will consume this metric instantly. Because `950.5` is way above the baseline (triggering a high Z-score), the `EventProcessor` will flag it as an anomaly.
Check the API:
```bash
# Note: If API_KEY is set in .env, add: -H "X-API-Key: your-key"
curl -s http://localhost:8000/anomalies
```
*(You should see the `payment-service` CPU event with a `critical` severity).*

### Step 5: View the AI Insight
The anomaly detection process automatically enqueued a request to the Gemini API. The `InsightWorker` picks this up and generates a root-cause analysis.
Wait a few seconds for the AI to respond, then fetch the insights:
```bash
# Note: If API_KEY is set in .env, add: -H "X-API-Key: your-key"
curl -s http://localhost:8000/insights | jq
```

**Example AI Output:**
```json
[
  {
    "pod": "payment-service",
    "namespace": "default",
    "root_cause": "The payment-service pod experienced a sudden, massive CPU utilization spike (value: 950.5) that significantly exceeds normal operational baselines.",
    "confidence": "High",
    "recommendation": "1. Check application logs for infinite loops or sudden bursts in transaction volume. 2. Verify if a recent deployment introduced a CPU regression. 3. Consider scaling up the replica count or increasing CPU limits if the load is legitimate.",
    "fallback": false
  }
]
```
If you see `"fallback": false`, congratulations! The **Gemini 3 Flash** model successfully analyzed your anomaly.

---

## Stopping Services

Stop the backend if you are running Option B locally: `Ctrl+C` in the terminal windows.

Stop Docker dependencies (Option A & B):

```bash
# Stop containers (keep data)
docker compose down

# Stop and delete PostgreSQL data volume
docker compose down -v
```
