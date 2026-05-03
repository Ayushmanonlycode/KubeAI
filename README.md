# AI Kubernetes Observability Backend

Production-oriented FastAPI backend for AI-driven Kubernetes pod observability. The backend collects Prometheus metrics, queues them in Redis, detects anomalies with deterministic rolling z-scores, correlates events with NetworkX, stores data in PostgreSQL, and uses Gemini only for reasoning and explanations.

Gemini is not required for availability. If Gemini is unavailable or the API key is missing, the backend keeps running and returns fallback insights.

## Architecture

```text
Kubernetes
  -> Prometheus
  -> Metrics Collector
  -> Redis Queue
  -> Detection Agents
  -> Correlation Engine
  -> Gemini Reasoning Engine
  -> FastAPI
  -> Dashboard
```

## What The Services Do

- Metrics collector polls Prometheus every 10 seconds.
- Collector gathers CPU, memory, disk I/O, network throughput, PVC usage, and pod restart count.
- Detection agents keep rolling windows of 50 values.
- An anomaly is created when `abs(z_score) > 2`.
- Correlation engine creates dependency edges when events occur within 5 seconds and correlation is greater than `0.8`.
- Gemini uses `gemini-2.5-flash` for root cause explanations and recommendations.
- PostgreSQL stores metrics, anomalies, and insights.
- Redis is used as the event queue.

## 1. Go To The Project Directory

Most command errors happen when you are in `~` instead of the project folder.

For fish shell:

```fish
cd "/home/ayu/ABB _PROJECT"
```

Check the files:

```fish
ls
```

You should see files and folders like:

```text
app
requirements.txt
docker-compose.yml
.env
.venv
```

## 2. Activate The Virtual Environment

For fish:

```fish
source .venv/bin/activate.fish
```

If activation works, your shell prompt may show `(.venv)`.

You can also skip activation and call binaries directly:

```fish
.venv/bin/python
.venv/bin/pip
.venv/bin/uvicorn
```

## 3. Install Python Requirements

Only needed once:

```fish
.venv/bin/pip install -r requirements.txt
```

Verify the FastAPI app imports:

```fish
.venv/bin/python -c "from app.main import app; print(app.title)"
```

Expected output:

```text
AI Kubernetes Observability Backend
```

## 4. Start Local Dependencies

Start Redis, PostgreSQL, and Prometheus:

```fish
docker compose up -d redis postgres prometheus
```

Check status:

```fish
docker compose ps
```

Expected status:

```text
redis       Up / healthy
postgres    Up / healthy
prometheus  Up / healthy
```

This project maps PostgreSQL to host port `55432` because local port `5432` may already be used by your system PostgreSQL.

## 5. Configure Environment

Open `.env`:

```fish
nano .env
```

For local testing, use:

```env
PROMETHEUS_URL=http://localhost:9090
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql://observability:observability@localhost:55432/observability
GEMINI_API_KEY=replace-with-your-gemini-api-key
GEMINI_MODEL=gemini-2.5-flash
METRICS_POLL_INTERVAL_SECONDS=10
```

Replace the Gemini line with your real API key:

```env
GEMINI_API_KEY=AIza...
```

If you do not set a real key, `/health` will show:

```json
{"gemini":"fallback"}
```

That is expected. The backend still runs.

## 6. Start The Backend

Run:

```fish
cd "/home/ayu/ABB _PROJECT"
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Expected logs:

```text
Application startup complete.
Uvicorn running on http://0.0.0.0:8000
database_connected
metric_collected
```

Keep this terminal open. Open another terminal for curl commands.

## 7. Test The API

Health:

```fish
curl http://localhost:8000/health
```

Expected with no Gemini key:

```json
{"database":"ok","queue":"ok","collector":"ok","gemini":"fallback","collector_error":null,"gemini_error":null}
```

Recent metrics:

```fish
curl http://localhost:8000/metrics/recent
```

System metrics:

```fish
curl http://localhost:8000/system/metrics
```

Anomalies:

```fish
curl http://localhost:8000/anomalies
```

Dependency graph:

```fish
curl http://localhost:8000/dependencies
```

Gemini insights:

```fish
curl http://localhost:8000/insights
```

## 8. Why Metrics May Be Empty

If `/metrics/recent` returns:

```json
[]
```

then the backend is running, but Prometheus does not have Kubernetes pod metrics.

Check Prometheus directly:

```fish
curl -s "http://localhost:9090/api/v1/query?query=container_cpu_usage_seconds_total"
```

If the response is:

```json
{"status":"success","data":{"resultType":"vector","result":[]}}
```

then local Prometheus is reachable, but it is only scraping itself. It does not have Kubernetes/cAdvisor metrics.

This means:

```text
backend -> Prometheus API -> works
Prometheus -> Kubernetes pod metrics -> missing
```

The backend is ready. You need to connect it to a Kubernetes Prometheus instance.

## 9. Connect To Kubernetes Prometheus

Find Prometheus services in your cluster:

```fish
kubectl get svc -A | grep -i prometheus
```

Common service names:

```text
monitoring prometheus-server
monitoring kube-prometheus-stack-prometheus
```

For `prometheus-server`, port-forward with:

```fish
kubectl -n monitoring port-forward svc/prometheus-server 9090:80
```

For `kube-prometheus-stack-prometheus`, port-forward with:

```fish
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

Keep the port-forward terminal open.

Then test:

```fish
curl -s "http://localhost:9090/api/v1/query?query=container_cpu_usage_seconds_total"
```

If `"result"` contains data, the backend will start storing metrics.

## 10. Restart Backend After Config Changes

Stop Uvicorn with `Ctrl+C` in the terminal where it is running.

Then restart:

```fish
cd "/home/ayu/ABB _PROJECT"
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

If port `8000` is already used:

```fish
ss -ltnp | grep 8000
```

Use another port if needed:

```fish
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Then use:

```fish
curl http://localhost:8001/health
```

## 11. Stop Local Dependencies

Stop Docker services:

```fish
docker compose down
```

Stop and delete PostgreSQL data too:

```fish
docker compose down -v
```

Only use `-v` when you want to remove the local database volume.

## 12. Expected Final State

When everything is configured:

```fish
curl http://localhost:8000/health
```

Expected:

```json
{
  "database": "ok",
  "queue": "ok",
  "collector": "ok",
  "gemini": "ok"
}
```

And:

```fish
curl http://localhost:8000/metrics/recent
```

should return metric rows instead of:

```json
[]
```

## 13. Docker Build

Build the backend image:

```fish
docker build -t ai-observability-backend:latest .
```

Run it:

```fish
docker run --rm -p 8000:8000 --env-file .env ai-observability-backend:latest
```

## 14. Kubernetes Manifests

Example manifests are in `k8s/`.

Apply them:

```fish
kubectl apply -f k8s/secret.example.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

Before production use, replace the example secret values with real `DATABASE_URL` and `GEMINI_API_KEY`.

## Troubleshooting

If `uvicorn` is not found, use the venv binary:

```fish
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

If `source .venv/bin/activate.fish` says file not found, you are probably not in the project directory:

```fish
cd "/home/ayu/ABB _PROJECT"
source .venv/bin/activate.fish
```

If `pkill` says operation not permitted, stop Uvicorn with `Ctrl+C` in the terminal that started it, or run your own server on another port:

```fish
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001
```

If Prometheus query returns empty results:

```json
"result":[]
```

then Prometheus has no Kubernetes metrics. Connect to the cluster Prometheus using `kubectl port-forward`.
