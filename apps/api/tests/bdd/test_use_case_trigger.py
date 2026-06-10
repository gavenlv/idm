"""BDD: Use Case Trigger & Re-scan API."""
from pytest_bdd import scenarios

# 加载 feature 中的所有 Scenario, 自动生成测试函数
scenarios("use_case_trigger.feature")
