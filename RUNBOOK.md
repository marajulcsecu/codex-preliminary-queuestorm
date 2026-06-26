# RUNBOOK — QueueStorm Investigator

This runbook is the **bring-up guide** for a stranger (or a judge) who needs
to clone, run, test, and deploy this service end-to-end with copy-pasteable
commands.

> **Total time:** ~10 minutes from a clean machine.

---

## 1. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Python | 3.11+ | `python3 --version` |
| pip | 23+ | `pip --version` |
| git | any recent | `git --version` |
| Docker (optional) | 24+ | `docker --version` |

That's it. No GPU, no database, no cloud account required for local dev.

---

## 2. Local development (5 minutes)

### 2.1 Clone & install

```bash
git clone https://github.com/marajulcsecu/codex-preliminary-queuestorm.git
cd codex-preliminary-queuestorm

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

Expected: `Successfully installed fastapi-0.115.0 uvicorn-0.32.0 ...`

### 2.2 Run the server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Expected output:

```
INFO:     Started server process [1]
INFO:     Application startup complete.
INFO:     Uvicorn running on 0.0.0.0:8000 (Press CTRL+C to quit)
```

### 2.3 Hit the endpoints

In a second terminal:

```bash
# Health check
curl http://127.0.0.1:8000/health
# Expected: {"status":"ok"}

# Minimal analyze-ticket
curl -X POST http://127.0.0.1:8000/analyze-ticket \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"TKT-001","complaint":"hello there"}'
# Expected: 200 with full AnalyzeResponse JSON, case_type="other"

# SAMPLE-01 wrong-transfer
curl -X POST http://127.0.0.1:8000/analyze-ticket \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_id": "S01",
    "complaint": "I sent 5000 taka to a wrong number",
    "transaction_history": [{
      "transaction_id": "TXN-9101",
      "timestamp": "2026-04-14T14:08:22Z",
      "type": "transfer",
      "amount": 5000,
      "counterparty": "+8801719876543",
      "status": "completed"
    }]
  }'
# Expected: case_type="wrong_transfer", severity="high", department="dispute_resolution"
```

### 2.4 Stop the server

`Ctrl+C` in the first terminal.

---

## 3. Run the test suite

### 3.1 Pytest (full suite)

```bash
source .venv/bin/activate
pytest tests/ -v
```

Expected: **209 passed in ~1s**

### 3.2 Schema smoke test (5 critical checks)

```bash
source .venv/bin/activate
# First start the server in another terminal (see 2.2)
python scripts/smoke_schema.py 8000
```

Expected:

```
=== 11 passed, 0 failed in <200ms ===
```

### 3.3 End-to-end sample cases (10 cases × 6 dimensions)

```bash
source .venv/bin/activate
python scripts/run_samples.py 8000
```

Expected: 10 rows, all PASS, 60/60 dimensions match.

### 3.4 Latency benchmark

```bash
source .venv/bin/activate
python scripts/benchmark.py 8000 --requests 100 --warmup 10
```

Expected: p95 well under 5000ms. On modern hardware typically p95 ≈ 2ms.

---

## 4. Deploy to Railway (production)

> **Live URL:** https://codex-preliminary-api-production.up.railway.app

### 4.1 First-time setup

1. **Create a Railway account** at https://railway.app (login with GitHub).
2. **Grant Railway access** to your GitHub repos (or specifically to
   `marajulcsecu/codex-preliminary-queuestorm`).
3. From the Railway dashboard, click **"New Project"** → **"Deploy from GitHub repo"**.
4. Select `marajulcsecu/codex-preliminary-queuestorm`.
5. Railway starts building immediately using the `Dockerfile` (the
   `railway.json` declares `builder: DOCKERFILE`).

### 4.2 Configure env vars

In the service panel → **Variables** tab → add:

| Variable | Value |
|---|---|
| `PORT` | `8000` |
| `LOG_LEVEL` | `INFO` |

### 4.3 Verify the build settings

In the service panel → **Settings** tab → **Deploy**:

- **Builder:** `DOCKERFILE`
- **Start Command:** *(empty — the Dockerfile CMD owns port binding)*
- **Healthcheck Path:** `/health`
- **Healthcheck Timeout:** `180` (seconds)

> **Common pitfall:** Do NOT set a custom start command in the dashboard
> Settings. Railway overrides the Dockerfile CMD in exec form, which does
> not expand `$PORT`. Leave the dashboard Start Command blank.

### 4.4 Generate a public domain

In the service panel → **Settings** tab → **Networking** → click
**"Generate Domain"**. Railway assigns a URL like
`your-service-name-production.up.railway.app`.

### 4.5 Verify production

```bash
curl https://your-service-name-production.up.railway.app/health
# Expected: {"status":"ok"}

# Full sample suite against production
source .venv/bin/activate
python scripts/run_samples.py https://your-service-name-production.up.railway.app
```

Expected: 10/10 PASS, ~600ms per case.

### 4.6 Subsequent deploys

Pushes to `main` auto-deploy. No manual action needed.

```bash
git push origin main
# Railway rebuilds in ~60s, no downtime (zero-downtime deploy)
```

---

## 5. Render fallback (if Railway breaks)

`render.yaml` is committed and ready for one-click deploy:

1. Go to https://render.com → New → Web Service.
2. Connect the GitHub repo `marajulcsecu/codex-preliminary-queuestorm`.
3. Render auto-detects `render.yaml` and applies the settings.
4. Wait for first build (~2 minutes), then hit the assigned URL.

Environment variables (`PORT=8000`, `LOG_LEVEL=INFO`) are pre-configured in
`render.yaml`.

---

## 6. Docker (any host)

```bash
docker build -t queuestorm-investigator .
docker run -p 8000:8000 -e PORT=8000 queuestorm-investigator
```

Image size: ~65 MB (Python 3.11-slim + FastAPI + Uvicorn).

Verify:

```bash
curl http://localhost:8000/health
```

---

## 7. Troubleshooting

### "Address already in use" on port 8000

```bash
# Find and kill the process
lsof -ti:8000 | xargs kill -9
# Or use a different port
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

### Railway: `Invalid value for '--port': '$PORT' is not a valid integer`

You have a dashboard-level Start Command that overrides the Dockerfile.
**Fix:** Settings → Deploy → clear the Start Command field (leave blank).
The Dockerfile CMD uses shell form (`sh -c "... --port ${PORT:-8000}"`) which
expands `$PORT` correctly.

### `pip install` fails with "no matching distribution"

You're on an old Python. Use 3.11+:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Test suite passes locally but fails on Railway

Check `scripts/deploy_smoke.txt` for the captured live curl results. If
production returns 5xx, check the Railway deployment logs (Deployments tab →
View logs).

### A sample case fails after a code change

Run the regression gate:

```bash
python scripts/run_samples.py 8000
```

If any case shows MISMATCH, the change broke reasoning. Fix before committing.

---

## 8. File-by-file map (for code review)

| Path | What it does |
|---|---|
| `app/main.py` | FastAPI app, error handlers, the two endpoints |
| `app/models.py` | Pydantic v2 request/response schemas |
| `app/evidence.py` | 5-signal scorer + ambiguity + established-recipient |
| `app/classifier.py` | 8-rule case-type cascade |
| `app/routing.py` | Department routing table |
| `app/safety.py` | Negation-aware scanner + safe templates |
| `app/i18n.py` | Language detection + 16 reply templates |
| `app/reasoning.py` | Orchestrator (single public function `investigate`) |
| `tests/test_*.py` | Pytest suites |
| `scripts/smoke_schema.py` | 11-check schema smoke |
| `scripts/run_samples.py` | 10-case E2E vs expected |
| `scripts/benchmark.py` | Latency benchmark |
| `scripts/deploy_smoke.txt` | Captured production curl results |
| `sample_output.json` | Captured production response |
| `Dockerfile` | Multi-stage Docker image |
| `railway.json` | Railway config (uses DOCKERFILE builder) |
| `render.yaml` | Render fallback config |

---

## 9. Time budget for first-time bring-up

| Step | Time |
|---|---|
| Clone + install | 1 min |
| Start server | 10 sec |
| Hit endpoints | 30 sec |
| Run test suite | 5 sec |
| Deploy to Railway | 3 min |
| Generate domain | 10 sec |
| Verify production | 30 sec |
| **Total** | **~5 min** |

---

## 10. Where to get help

- **Live service:** `https://codex-preliminary-api-production.up.railway.app`
- **GitHub repo:** https://github.com/marajulcsecu/codex-preliminary-queuestorm
- **README:** [`README.md`](./README.md)
- **Architecture walkthrough:** see `docs/ARCHITECTURE.md` (gitignored, internal)