# ml-service (FastAPI)

Runs a local ML inference HTTP service consumed by Go collector.

## Run (local)

```bash
cd ml-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 9000
```

## Health check

```bash
curl -s http://127.0.0.1:9000/healthz | jq .
```

## Predict

Go collector calls:

- POST `http://127.0.0.1:9000/predict`
- env: `ML_SERVICE_URL` (optional)
