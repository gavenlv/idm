# BDD 入口配置
# 用 pytest-bdd 把 Gherkin feature 文件与 Python step definitions 绑定。
# 跑法: `pytest apps/api/tests/bdd/` 或 `make test-bdd`
#
# 设计: 每个 feature 对应一个用户故事 (User Story),
# 每个 scenario 是一组 step, 对应 Python step function。
# 复用现有 conftest.py 的 app_with_db / client fixture, 不引入新依赖。

[pytest]
testpaths = tests
bdd_features_base_dir = tests/bdd/features
asyncio_mode = auto
addopts = -ra --tb=short
