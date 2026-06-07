# IDM Use Case Schemas (v1)

Use Case YAML/JSON 的标准格式。

详见:
- 规范: [../docs/design/use-case-spec.md](../docs/design/use-case-spec.md)
- 示例: [../shop-orders-daily.yml](../shop-orders-daily.yml)
- 模板: [../_templates/basic.yml](../_templates/basic.yml)
- 校验: 使用 `use_cases/schema.json` (JSON Schema Draft 2020-12)

## 目录约定

```
use_cases/
├── schema.json              # JSON Schema 校验
├── _templates/
│   └── basic.yml            # 空白模板
├── prod/                    # 生产 use cases (走 ArgoCD sync)
├── staging/                 # 预发 (先验证)
└── test/                    # 单测用
```

## 提交流程

1. Copy `_templates/basic.yml` → `<env>/<id>.yml`
2. 填字段 (必填: id / version / description / sources / analysis)
3. 跑校验: `make validate-uc FILE=use_cases/staging/shop-orders-daily.yml`
4. `git commit` → IDM 自动监听 → Planner 调度
