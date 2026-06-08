"""etl_orders_daily: 订单宽表每日 ETL (阶段 1 上游预处理).

本 DAG 模拟真实数据管道的 Airflow 部分:
  1. extract_from_gcs  : 从 GCS company-raw 拉 CSV
  2. transform_with_flink : 调 Flink 预处理 (fct_orders_enriched)
  3. write_to_gcs_model_input : 输出到 GCS company-model-input (阶段 2)
  4. trigger_mex_inference   : 触发 MEX 模型推理
  5. load_mex_output_to_gcs  : MEX 输出到 GCS company-model-output (阶段 4)
  6. load_to_clickhouse      : Flink load → ClickHouse shop.fct_orders_risk_daily (阶段 5)
  7. refresh_superset_dashboards : 触发 Superset refresh (阶段 6)

SQL 片段会被 parse_airflow_dag Skill 抽取出来, 用于血缘推断。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator


# === DAG 默认参数 ===
default_args = {
    "owner": "data-platform",
    "depends_on_past": False,
    "start_date": datetime(2026, 1, 1),
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email": ["data-alerts@example.com"],
    "email_on_failure": True,
}


# === DAG 定义 ===
with DAG(
    dag_id="etl_orders_daily",
    default_args=default_args,
    description="订单宽表每日 ETL — 6 阶段真实管道演示",
    schedule_interval="0 2 * * *",   # 每天凌晨 2 点
    catchup=False,
    tags=["orders", "tier-1", "mex"],
) as dag:

    # 1) 从 GCS 拉数据
    extract = BashOperator(
        task_id="extract_from_gcs",
        bash_command=(
            "gsutil -m cp -r gs://company-raw/orders/{{ ds_nodash }}/ "
            "/tmp/orders_raw/"
        ),
    )

    # 2) Flink 预处理 (含 SQL 血缘线索)
    transform = BashOperator(
        task_id="transform_with_flink",
        bash_command=(
            "flink run -c com.example.OrdersPreprocessJob "
            "gs://company-jars/flink-orders-preprocess-1.0.jar "
            "--input /tmp/orders_raw/ "
            "--output gs://company-model-input/orders/{{ ds_nodash }}/ "
            "--sql 'INSERT INTO shop_gcs.orders_enriched "
            "SELECT order_id, user_id, order_date, total_amount, currency, status, "
            "       item_count, gross_amount, net_amount, tax_amount, payment_method, "
            "       is_first_order, days_since_signup "
            "FROM shop_gcs.orders_raw WHERE order_date = CURRENT_DATE'"
        ),
    )

    # 3) 写 model-input
    write_model_input = BashOperator(
        task_id="write_to_gcs_model_input",
        bash_command=(
            "echo 'model-input ready at gs://company-model-input/orders/{{ ds_nodash }}/'"
        ),
    )

    # 4) 触发 MEX
    trigger_mex = BashOperator(
        task_id="trigger_mex_inference",
        bash_command=(
            "curl -X POST http://mex-service.example.com/infer/orders_risk_model "
            "-d '{\"date\":\"{{ ds }}\",\"input\":\"gs://company-model-input/orders/{{ ds_nodash }}/\"}'"
        ),
    )

    # 5) 收集 MEX 输出
    collect_mex = BashOperator(
        task_id="load_mex_output_to_gcs",
        bash_command=(
            "gsutil -m cp -r gs://company-model-output/orders/{{ ds_nodash }}/ "
            "/tmp/orders_risk/"
        ),
    )

    # 6) load to ClickHouse
    load_ch = BashOperator(
        task_id="load_to_clickhouse",
        bash_command=(
            "clickhouse-client --query \""
            "INSERT INTO shop.fct_orders_risk_daily "
            "SELECT order_id, user_id, order_date, risk_score, risk_label, "
            "       fraud_prob, chargeback_prob, model_version, model_run_at "
            "FROM s3('https://company-model-output.s3.amazonaws.com/orders/{{ ds_nodash }}/*.csv', "
            "       'CSV', 'order_id String, user_id UInt64, order_date Date, "
            "              risk_score Float64, risk_label String, fraud_prob Float64, "
            "              chargeback_prob Float64, model_version String, model_run_at DateTime')"
            "\""
        ),
    )

    # 7) refresh Superset
    refresh_ss = BashOperator(
        task_id="refresh_superset_dashboards",
        python_callable=lambda: print("refresh Superset dashboard id=1,2"),
    )

    # === Task 依赖 (DAG 拓扑) ===
    extract >> transform >> write_model_input >> trigger_mex >> collect_mex >> load_ch >> refresh_ss
