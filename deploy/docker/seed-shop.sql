-- IDM dev seed: 模拟一个订单宽表治理的 ClickHouse schema
-- 包含: 用户, 订单, 支付, 商品 (含 PII 列)

CREATE TABLE IF NOT EXISTS shop.users (
    user_id UInt64,
    email String,
    phone String,
    name String,
    register_time DateTime DEFAULT now(),
    country LowCardinality(String)
) ENGINE = MergeTree()
ORDER BY user_id;

CREATE TABLE IF NOT EXISTS shop.orders_daily (
    order_id String,
    user_id UInt64,
    order_date Date,
    status LowCardinality(String),
    total_amount Decimal(18, 2),
    currency LowCardinality(String) DEFAULT 'CNY',
    payment_method LowCardinality(String),
    shipping_address String,
    created_at DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(order_date)
ORDER BY (order_date, user_id);

CREATE TABLE IF NOT EXISTS shop.payments (
    payment_id String,
    order_id String,
    paid_at DateTime,
    amount Decimal(18, 2),
    card_bin String,
    status LowCardinality(String)
) ENGINE = MergeTree()
ORDER BY (order_id, paid_at);

CREATE TABLE IF NOT EXISTS shop.order_items (
    order_id String,
    sku_id String,
    quantity UInt32,
    unit_price Decimal(18, 2),
    discount_amount Decimal(18, 2) DEFAULT 0
) ENGINE = MergeTree()
ORDER BY (order_id, sku_id);

CREATE TABLE IF NOT EXISTS shop.products (
    sku_id String,
    name String,
    category LowCardinality(String),
    price Decimal(18, 2),
    stock_quantity Int32 DEFAULT 0
) ENGINE = ReplacingMergeTree()
ORDER BY sku_id;

CREATE TABLE IF NOT EXISTS shop.orders_temp_test (
    id UInt64,
    payload String
) ENGINE = Memory;

INSERT INTO shop.users VALUES (1, 'alice@example.com', '+8613800000001', 'Alice', '2024-01-15 10:00:00', 'CN');
INSERT INTO shop.users VALUES (2, 'bob@example.com',   '+8613800000002', 'Bob',   '2024-02-20 12:30:00', 'CN');
INSERT INTO shop.users VALUES (3, 'carol@example.com', '+8613800000003', 'Carol', '2024-03-10 09:15:00', 'US');

INSERT INTO shop.orders_daily VALUES ('O001', 1, '2024-06-01', 'paid',    199.00, 'CNY', 'alipay', 'Shanghai Pudong', '2024-06-01 10:30:00');
INSERT INTO shop.orders_daily VALUES ('O002', 2, '2024-06-01', 'paid',    89.50,  'CNY', 'wechat', 'Beijing Chaoyang','2024-06-01 11:00:00');
INSERT INTO shop.orders_daily VALUES ('O003', 3, '2024-06-02', 'pending', 1299.0, 'USD', 'stripe', 'NYC 5th Ave',     '2024-06-02 14:00:00');

INSERT INTO shop.payments VALUES ('P001', 'O001', '2024-06-01 10:35:00', 199.00, '622202', 'success');
INSERT INTO shop.payments VALUES ('P002', 'O002', '2024-06-01 11:05:00', 89.50,  '621700', 'success');

INSERT INTO shop.order_items VALUES ('O001', 'SKU-A', 1, 199.00, 0);
INSERT INTO shop.order_items VALUES ('O002', 'SKU-B', 2, 44.75, 0);
INSERT INTO shop.order_items VALUES ('O003', 'SKU-C', 1, 1299.0, 0);

INSERT INTO shop.products VALUES ('SKU-A', 'Keyboard',   'electronics', 199.00, 50);
INSERT INTO shop.products VALUES ('SKU-B', 'Coffee Mug', 'lifestyle',   44.75, 200);
INSERT INTO shop.products VALUES ('SKU-C', 'E-reader',   'electronics', 1299.0, 20);

-- === 6 阶段真实管道 — 阶段 5 落地表 (MEX 输出经 Flink load) ===
CREATE TABLE IF NOT EXISTS shop.fct_orders_risk_daily (
    order_id String,
    user_id UInt64,
    order_date Date,
    risk_score Float64,
    risk_label LowCardinality(String),
    fraud_prob Float64,
    chargeback_prob Float64,
    model_version LowCardinality(String),
    model_run_at DateTime
) ENGINE = ReplacingMergeTree(model_run_at)
PARTITION BY toYYYYMM(order_date)
ORDER BY (order_date, order_id);

INSERT INTO shop.fct_orders_risk_daily VALUES
    ('ORD-20260608-0001', 10001, '2026-06-08', 0.12, 'low',    0.02,  0.01,  'risk-v3.2.1', '2026-06-08 10:30:00'),
    ('ORD-20260608-0002', 10002, '2026-06-08', 0.78, 'high',   0.65,  0.22,  'risk-v3.2.1', '2026-06-08 10:30:00'),
    ('ORD-20260608-0003', 10003, '2026-06-08', 0.34, 'medium', 0.18,  0.08,  'risk-v3.2.1', '2026-06-08 10:30:00'),
    ('ORD-20260608-0004', 10004, '2026-06-08', 0.08, 'low',    0.01,  0.005, 'risk-v3.2.1', '2026-06-08 10:30:00'),
    ('ORD-20260608-0005', 10005, '2026-06-08', 0.45, 'medium', 0.31,  0.12,  'risk-v3.2.1', '2026-06-08 10:30:00');

-- === 6 阶段真实管道 — 阶段 5 业务事实表 (供 Superset 引用) ===
CREATE TABLE IF NOT EXISTS shop.fct_orders_daily (
    order_id String,
    user_id UInt64,
    order_date Date,
    total_amount Decimal(18, 2),
    currency LowCardinality(String),
    status LowCardinality(String),
    payment_method LowCardinality(String),
    item_count UInt32,
    country LowCardinality(String)
) ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(order_date)
ORDER BY (order_date, order_id);

INSERT INTO shop.fct_orders_daily VALUES
    ('ORD-20260608-0001', 10001, '2026-06-08', 328.50,  'CNY', 'paid',     'alipay',     3,  'CN'),
    ('ORD-20260608-0002', 10002, '2026-06-08', 1099.00, 'CNY', 'paid',     'wechat_pay', 5,  'CN'),
    ('ORD-20260608-0003', 10003, '2026-06-08', 45.20,   'CNY', 'refunded', 'credit_card',1,  'US'),
    ('ORD-20260608-0004', 10004, '2026-06-08', 876.30,  'CNY', 'paid',     'alipay',     8,  'CN'),
    ('ORD-20260608-0005', 10005, '2026-06-08', 212.00,  'CNY', 'pending',  'credit_card',2,  'CN');
