"""HTTP API and the small browser prototype."""

from pathlib import Path

from psycopg import OperationalError
from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from checkstock import inventory


app = FastAPI(title="CheckStock", version="0.1.0")
static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.exception_handler(inventory.InventoryError)
def inventory_error(_, exc: inventory.InventoryError):
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)


@app.exception_handler(OperationalError)
def database_error(_, __):
    return JSONResponse({"detail": "База данных недоступна"}, status_code=503)


@app.exception_handler(RequestValidationError)
def validation_error(_, exc: RequestValidationError):
    return JSONResponse({"detail": "Проверьте параметры запроса", "errors": exc.errors()}, status_code=422)


class Receipt(BaseModel):
    product_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=128)


class Transfer(BaseModel):
    product_id: int = Field(gt=0)
    source_warehouse_id: int = Field(gt=0)
    target_warehouse_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=128)


class WarehouseName(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@app.get("/")
def index():
    return FileResponse(static_dir / "index.html")


@app.get("/api/catalog")
def catalog():
    return inventory.catalog()


@app.get("/api/stock")
def stock():
    return inventory.stock_snapshot()


@app.get("/api/history")
def history(limit: int = Query(50, ge=1, le=100), before_id: int | None = Query(None, gt=0)):
    return inventory.list_history(limit, before_id)


@app.post("/api/receipts", status_code=201)
def receipt(body: Receipt):
    return inventory.receive_stock(body.product_id, body.warehouse_id, body.quantity, body.idempotency_key)


@app.post("/api/fbs/reservations", status_code=201)
def reserve(body: Receipt):
    return inventory.reserve_fbs(body.product_id, body.warehouse_id, body.quantity, body.idempotency_key)


@app.post("/api/fbs/reservations/{operation_id}/ship")
def ship_fbs(operation_id: int):
    return inventory.transition(operation_id, "ship_fbs")


@app.post("/api/fbs/reservations/{operation_id}/cancel")
def cancel_fbs(operation_id: int):
    return inventory.transition(operation_id, "cancel_fbs")


@app.post("/api/transfers", status_code=201)
def transfer(body: Transfer):
    return inventory.send_transfer(body.product_id, body.source_warehouse_id,
                                   body.target_warehouse_id, body.quantity, body.idempotency_key)


@app.post("/api/transfers/{operation_id}/receive")
def receive_transfer(operation_id: int):
    return inventory.transition(operation_id, "receive_transfer")


@app.post("/api/fbo/shipments", status_code=201)
def ship_fbo(body: Receipt):
    return inventory.send_fbo(body.product_id, body.warehouse_id, body.quantity, body.idempotency_key)


@app.post("/api/fbo/shipments/{operation_id}/receive")
def receive_fbo(operation_id: int):
    return inventory.transition(operation_id, "receive_fbo")


@app.post("/api/warehouses", status_code=201)
def add_warehouse(body: WarehouseName):
    return inventory.add_warehouse(body.name)


@app.patch("/api/warehouses/{warehouse_id}")
def rename_warehouse(warehouse_id: int, body: WarehouseName):
    return inventory.rename_warehouse(warehouse_id, body.name)


@app.delete("/api/warehouses/{warehouse_id}")
def archive_warehouse(warehouse_id: int):
    return inventory.archive_warehouse(warehouse_id)


@app.post("/api/warehouses/{warehouse_id}/restore")
def restore_warehouse(warehouse_id: int):
    return inventory.restore_warehouse(warehouse_id)
