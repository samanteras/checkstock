CREATE TABLE IF NOT EXISTS products (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS warehouses (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    archived_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS balances (
    product_id BIGINT NOT NULL REFERENCES products(id),
    warehouse_id BIGINT NOT NULL REFERENCES warehouses(id),
    physical INTEGER NOT NULL DEFAULT 0 CHECK (physical >= 0),
    reserved_fbs INTEGER NOT NULL DEFAULT 0 CHECK (reserved_fbs >= 0),
    PRIMARY KEY (product_id, warehouse_id),
    CONSTRAINT reserved_not_above_physical CHECK (reserved_fbs <= physical)
);

CREATE TABLE IF NOT EXISTS operations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('receipt', 'fbs', 'transfer', 'fbo')),
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    source_warehouse_id BIGINT REFERENCES warehouses(id),
    target_warehouse_id BIGINT REFERENCES warehouses(id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'shipped', 'completed', 'cancelled')),
    idempotency_key TEXT UNIQUE,
    fingerprint TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS movement_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id BIGINT NOT NULL REFERENCES operations(id),
    action TEXT NOT NULL,
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    source_warehouse_id BIGINT REFERENCES warehouses(id),
    target_warehouse_id BIGINT REFERENCES warehouses(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS operations_transit_idx ON operations (kind, status, product_id);
CREATE INDEX IF NOT EXISTS operations_open_idx ON operations (id DESC) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS movement_events_operation_idx ON movement_events (operation_id);
