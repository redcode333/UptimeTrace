"""Demo upstream service. Calls inventory-api; the W3C trace context is propagated by the httpx instrumentation."""
from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from common import setup_tracing

setup_tracing("frontend-api")
app = FastAPI()
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()
INVENTORY = os.environ.get("INVENTORY_URL", "http://127.0.0.1:8002")


@app.get("/checkout/{item_id}")
def checkout(item_id: str):
    with httpx.Client(timeout=10) as client:
        r = client.get(f"{INVENTORY}/stock/{item_id}")
    if r.status_code >= 500:
        raise HTTPException(status_code=502, detail="inventory unavailable")
    return {"item_id": item_id, "status": "ok"}
