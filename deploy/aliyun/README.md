# IDM 部署到阿里云 ACS — 完整指南

> 📌 **配套文档**: [AGENT_INSTRUCTIONS.md §10](../../docs/AGENT_INSTRUCTIONS.md) · [deployment.md](../../docs/design/deployment.md)
>
> 本指南专注于 **阿里云 ACS (按量计费, 最少使用原则)**

---

## 0. 目录

1. [前置条件](#1-前置条件)
2. [架构概览](#2-架构概览)
3. [镜像构建与推送](#3-镜像构建与推送)
4. [敏感信息管理 (KMS / Secrets Manager)](#4-敏感信息管理)
5. [K8s 部署清单说明](#5-k8s-部署清单说明)
6. [ALB Ingress + EIP 外网暴露](#6-alb-ingress--eip-外网暴露)
7. [一键部署](#7-一键部署)
8. [验证与排查](#8-验证与排查)
9. [成本估算 (按量计费)](#9-成本估算)
10. [下线 / 清理](#10-下线--清理)

---

## 1. 前置条件

| 类别 | 要求 |
| --- | --- |
| **阿里云账号** | 已开通 ACS / ACK / ACR / KMS / Secrets Manager / ALB / EIP |
| **kubectl** | >= 1.28 (ACS API 兼容 1.24+) |
| **Kubeconfig** | 已放置在 `idm/.kubeconfig` |
| **ACR 仓库** | 命名空间 + 仓库 `idm-api` / `idm-web` (公共/私有都行) |
| **Postgres** | 推荐阿里云 RDS PG 14 + AGE + pgvector, 或自建 (集群内) |
| **ClickHouse** | 推荐阿里云 ClickHouse (CDT) 或自建集群 |
| **域名** | 准备一个域名, 解析到 ALB EIP (可选) |
| **Aliyun CLI** | `aliyun configure` 已配置好 AccessKey |

---

## 2. 架构概览

```
              Internet
                 │
                 ▼
        ┌──────────────────┐
        │  EIP (Public IP) │   ← 阿里云 ALB 自动绑定
        │  ALB (slb.s1.s)  │   ← 按量计费
        └──────┬───────────┘
               │ :80
               ▼
       ┌────────────────────┐
       │  Ingress (ALB)     │
       │  ns: idm           │
       └────┬────────┬──────┘
            │ /      │ /api
            ▼        ▼
       ┌──────┐  ┌────────┐
       │ web  │  │  api   │   ← 1 副本起, HPA 1~4
       └──────┘  └───┬────┘
                     │ asyncpg
                     ▼
             ┌──────────────┐
             │ RDS PG 14    │  ← 外部 / 集群内
             │ +AGE+pgvector│
             └──────────────┘
                     │
                     ▼
             ┌──────────────┐
             │ ClickHouse   │  ← 外部 / 集群内
             │ (readonly)   │
             └──────────────┘
```

**外部依赖 (Postgres / ClickHouse) 可放 ACS 集群内 (推荐) 或外部云服务**。

---

## 3. 镜像构建与推送

### 3.1 本地构建并推送到 ACR

```bash
# 1) 登录 ACR
aliyun cr Login-Registry --region cn-hangzhou

# 2) 构建并推送 API
cd d:/workspace/github-ai/idm
docker build -t registry.cn-hangzhou.aliyuncs.com/your-ns/idm-api:0.1.0 -f apps/api/Dockerfile .
docker push registry.cn-hangzhou.aliyuncs.com/your-ns/idm-api:0.1.0

# 3) 构建并推送 Web
docker build -t registry.cn-hangzhou.aliyuncs.com/your-ns/idm-web:0.1.0 -f apps/web/Dockerfile .
docker push registry.cn-hangzhou.aliyuncs.com/your-ns/idm-web:0.1.0
```

### 3.2 在 ACS 集群里使用免密拉取 (推荐)

1. ACR 控制台: **访问凭证** → **固定密码** 或 **临时 Token**
2. 复制 username/password, 用 `kubectl create secret docker-registry` 创建:
   ```bash
   kubectl create secret docker-registry acr-registry \
     --docker-server=registry.cn-hangzhou.aliyuncs.com \
     --docker-username=your-user \
     --docker-password=your-pass \
     -n idm
   ```
3. 在 base/api-deployment.yaml / base/web-deployment.yaml 的 `spec.template.spec.imagePullSecrets` 加 `- name: acr-registry`
   (kustomize patch 即可, 见 overlays/acs/image-pull-secret-patch.yaml)

---

## 4. 敏感信息管理

### 4.1 强烈推荐: 阿里云 KMS + Secrets Manager

```bash
# 在阿里云 KMS 创建 CMK: idm-secret-key (Aliyun_KMS 区域 cn-hangzhou)
# 在 Secrets Manager 创建 Secret: idm-api-env, 内容是 .env 文件整体
# 启用 CSI Secret Store Driver, 创建 SecretProviderClass (见 deploy/k8s/secrets/spc.yaml)

# 然后 Pod 通过 volumeMount 自动挂载 /secrets/idm-api-env
```

**优势**:
- Secret 内容在阿里云控制台加密存储
- 审计 Secret 访问
- 自动轮转密钥
- 不需要把 .env 内容 commit 到 Git

### 4.2 临时方案: 阿里云 KMS 加密的 K8s Secret

```bash
# 1) 复制模板
cp overlays/acs/secrets-kms.yaml.example overlays/acs/secrets-kms.yaml

# 2) 把所有 REPLACE_ME 替换为实际值
# 3) 配置 alibabacloud-encryption-provider (阿里云 K8s 服务默认带)
# 4) 应用:
kubectl apply -f overlays/acs/secrets-kms.yaml
```

### 4.3 本地测试: 明文 base64 Secret (⚠️  仅 dev)

```bash
cp overlays/acs/secrets.yaml.example overlays/acs/secrets.yaml
# 编辑 .yaml 替换 REPLACE_ME
kubectl apply -f overlays/acs/secrets.yaml
```

### 4.4 .env 模板与 Secret 字段对照

`.env` 里的 **所有敏感字段** 都进 Secret (`idm-api-env`):
- `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` (LLM)
- `CLICKHOUSE_PASSWORD` (DB)
- `MCP_GITHUB_TOKEN`, `MCP_SLACK_BOT_TOKEN` (集成)
- `LANGFUSE_SECRET_KEY` (观测)

**非敏感** 字段进 ConfigMap (`idm-api-config`):
- 模型名、池大小、时区、MCP transport

完整对照见 [overlays/acs/secrets.yaml.example](../overlays/acs/secrets.yaml.example) 和 [base/api-config.yaml](../base/api-config.yaml)。

---

## 5. K8s 部署清单说明

| 文件 | 作用 |
| --- | --- |
| `base/namespace.yaml` | `idm` namespace |
| `base/serviceaccount.yaml` | `idm-api` / `idm-web` SA (含 RRSA 注解) |
| `base/api-deployment.yaml` | FastAPI 1 副本, 资源 250m/256Mi, 1c/1Gi |
| `base/api-service.yaml` | ClusterIP 8080 |
| `base/web-deployment.yaml` | nginx 静态 1 副本, 50m/64Mi |
| `base/web-service.yaml` | ClusterIP 80 |
| `base/api-config.yaml` | 非敏感 ConfigMap |
| `base/web-config.yaml` | nginx.conf (含 /api 反代) |
| `base/api-hpa.yaml` | API 1~4 副本 HPA, Web 1~2 副本 |
| `base/pdb.yaml` | PDB minAvailable=1 |
| `base/network-policies.yaml` | NetworkPolicy (出口白名单 LLM/CH/PG) |
| `base/ingress-class.yaml` | alb / nginx IngressClass |
| `overlays/acs/ingress-alb.yaml` | ALB Ingress (EIP 暴露) |
| `overlays/acs/secrets-kms.yaml.example` | KMS 加密 Secret 模板 |
| `overlays/acs/kustomization.yaml` | ACS 部署 (镜像 patch, 资源调小) |

---

## 6. ALB Ingress + EIP 外网暴露

### 6.1 自动创建 ALB + EIP (推荐)

`overlays/acs/ingress-alb.yaml` 的注解:
- `alb.ingress.kubernetes.io/name: idm-alb` — ALB 实例名
- `alb.ingress.kubernetes.io/instance-type: "slb.s1.small"` — 按量最小规格
- `alb.ingress.kubernetes.io/address-type: internet` — 面向公网
- `alb.ingress.kubernetes.io/binding-eip: "true"` — **自动申请并绑定 EIP**

部署后 ALB 控制台会看到:
- 实例: `idm-alb` (按量计费)
- 监听: HTTP/80
- EIP: `自动申请` (公网访问入口)

### 6.2 关联已有 EIP (可选, 固定 IP)

如果你已经有 EIP, 改用:
```yaml
alb.ingress.kubernetes.io/eip-id: "eip-xxxxxxxxxx"
```

### 6.3 HTTPS (可选, M2+)

```yaml
alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS":443,"HTTP":80}]'
alb.ingress.kubernetes.io/cert-manager/ssl-issuer: "letsencrypt-prod"  # 或使用阿里云证书
```

---

## 7. 一键部署

### 7.1 准备 Kubeconfig

```bash
# 阿里云 ACS 控制台: 集群 -> 连接信息 -> 复制 kubeconfig
# 保存到 idm/.kubeconfig (当前已就绪)
export KUBECONFIG=d:/workspace/github-ai/idm/.kubeconfig
```

### 7.2 一键脚本 (PowerShell)

```powershell
# 1) 准备 Secret
cd d:/workspace/github-ai/idm
Copy-Item deploy/k8s/overlays/acs/secrets.yaml.example deploy/k8s/overlays/acs/secrets.yaml
# 用编辑器打开 secrets.yaml, 替换所有 REPLACE_ME

# 2) 创建 namespace + secret
kubectl apply -f deploy/k8s/overlays/acs/secrets.yaml

# 3) 部署 base + overlay
kubectl apply -k deploy/k8s/overlays/acs

# 4) 等 30s
Start-Sleep -Seconds 30

# 5) 检查
kubectl get pods -n idm
kubectl get svc -n idm
kubectl get ingress -n idm
```

### 7.3 一键脚本 (Bash)

```bash
cd /d/workspace/github-ai/idm

# 1) Secret
cp deploy/k8s/overlays/acs/secrets.yaml.example deploy/k8s/overlays/acs/secrets.yaml
# 编辑

# 2) Secret
kubectl apply -f deploy/k8s/overlays/acs/secrets.yaml

# 3) Kustomize
kubectl apply -k deploy/k8s/overlays/acs

# 4) 等
sleep 30

# 5) 检查
kubectl get pods,svc,ingress -n idm
```

### 7.4 自动 Deploy 脚本

```bash
# 用 deploy/aliyun/deploy.sh (本目录) 一键跑完所有步骤
# 该脚本: timeout 30s / 不阻塞 / 自动续行
bash deploy/aliyun/deploy.sh
```

---

## 8. 验证与排查

### 8.1 基本检查

```bash
# Pod
kubectl get pods -n idm -o wide
# 期望: idm-api-xxx (1/1 Running), idm-web-xxx (1/1 Running)

# Service
kubectl get svc -n idm

# Ingress / ALB
kubectl get ingress -n idm
# 期望: ADDRESS = EIP 公网 IP

# 日志
kubectl logs -n idm -l app.kubernetes.io/component=api --tail=100
kubectl logs -n idm -l app.kubernetes.io/component=web --tail=100

# 进入 Pod
kubectl exec -it -n idm <api-pod> -- /bin/sh
```

### 8.2 健康检查

```bash
# 内网
kubectl exec -n idm <api-pod> -- curl http://localhost:8080/health
# 期望: {"status":"ok",...}

# 公网 (替换为 EIP)
curl http://<EIP>/health
curl http://<EIP>/api/v1/skills
```

### 8.3 常见问题

| 问题 | 排查 |
| --- | --- |
| Pod 卡在 `ImagePullBackOff` | 检查 ACR 镜像 + `imagePullSecrets` |
| Pod 一直重启 | `kubectl logs` 看 crash, 大概率是 `DATABASE_URL` 错 |
| ALB 创建失败 | 检查 ACS 集群是否装了 alb-ingress 组件 |
| EIP 没分配 | 检查 RAM 权限 (VPC + EIP 创建) |
| 502 Bad Gateway | API pod 没起来, 看 `kubectl describe pod` |

### 8.4 阿里云日志收集

```bash
# 阿里云 ACS 默认集成 SLS
kubectl logs -n idm -l app.kubernetes.io/component=api | tee /tmp/api.log
# 或在阿里云控制台: 集群 -> 日志
```

---

## 9. 成本估算 (按量计费, 最小使用)

| 资源 | 规格 | 月成本 (CNY) |
| --- | --- | --- |
| **ACS Pod (API)** | 1 副本 0.25c/256Mi | ~15 |
| **ACS Pod (Web)** | 1 副本 0.05c/64Mi | ~5 |
| **ALB (slb.s1.small)** | 按量 + 1 rule | ~30 |
| **EIP** | 按使用, 1 Mbps | ~20 |
| **RDS PG HA** | 1c/2Gi | ~100 |
| **ClickHouse (单节点)** | 2c/8Gi | ~150 |
| **ACR 镜像** | 1 GB | ~1 |
| **LLM API (DeepSeek)** | ~50 万 tokens/日 | ~150 |
| **KMS / Secrets** | 1000 次访问 | ~5 |
| **合计** | - | **~480 / 月** |

> 起步 MVP, 日均 100 次 API 调用规模。
> 流量起来后再加 HPA 副本 / ALB 升级 / 带宽升级。

---

## 10. 下线 / 清理

```bash
# 删除所有资源
kubectl delete -k deploy/k8s/overlays/acs

# 删除 Secret
kubectl delete secret -n idm idm-api-env

# 删除 namespace (含所有资源)
kubectl delete namespace idm
```

---

## 附录 A: 故障 Runbook

### A.1 重启 API

```bash
kubectl rollout restart deployment/idm-api -n idm
kubectl rollout status deployment/idm-api -n idm
```

### A.2 更新镜像

```bash
# 1) build & push 新镜像
docker build -t registry.../idm-api:0.1.1 -f apps/api/Dockerfile .
docker push registry.../idm-api:0.1.1

# 2) 改 overlays/acs/kustomization.yaml 的 newTag
# 3) apply
kubectl apply -k deploy/k8s/overlays/acs
```

### A.3 DB 迁移 (alembic)

```bash
# 临时跑迁移 pod
kubectl run alembic -n idm --rm -it --restart=Never \
  --image=registry.../idm-api:0.1.0 \
  --env="DATABASE_URL=postgresql+asyncpg://idm:pass@idm-pg:5432/idm" \
  --command -- alembic upgrade head
```

---

> 📌 **配套阅读**: [deployment.md](../../docs/design/deployment.md) · [AGENT_INSTRUCTIONS.md](../../docs/AGENT_INSTRUCTIONS.md)
