"""Demo downstream service. Its work is a child span named like a database call; `slow-` and `broken-` ids exercise latency and errors."""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from common import setup_tracing

setup_tracing("inventory-api")
app = FastAPI()
FastAPIInstrumentor.instrument_app(app)
tracer = trace.get_tracer("inventory-api")


@app.get("/stock/{item_id}")
def stock(item_id: str):
    with tracer.start_as_current_span("inventory.query") as span:
        span.set_attribute("inventory.item_id", item_id)
        if item_id.startswith("slow-"):
            time.sleep(1.2)
        if item_id.startswith("broken-"):
            span.set_status(trace.Status(trace.StatusCode.ERROR, "stock lookup failed"))
            raise HTTPException(status_code=500, detail="stock lookup failed")
        return {"item_id": item_id, "in_stock": 7}
