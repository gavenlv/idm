"""BDD: Data Pipeline (GCS / Flink / Superset / Airflow)."""
from pytest_bdd import scenarios

# 加载 feature 中的所有 Scenario, 自动生成测试函数
scenarios("data_pipeline.feature")
