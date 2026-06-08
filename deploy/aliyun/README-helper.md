# IDM 阿里云 ACS 部署 — 配套脚本

> 📌 **主文档**: [README.md](./README.md) — 完整部署指南
>
> 本目录提供 **一键脚本 + 工具**。

---

## 文件清单

| 文件 | 用途 |
| --- | --- |
| `README.md` | 完整部署文档 (前置 / 镜像 / Secret / Ingress / 验证) |
| `deploy.sh` | 一键部署脚本 (timeout 30s, 不阻塞) |
| `rollback.sh` | 一键回滚 (delete all) |
| `port-forward.sh` | 本地端口转发调试 (kubectl port-forward) |

---

## 快速开始

```bash
# 1) 准备 kubeconfig
export KUBECONFIG=/path/to/idm/.kubeconfig

# 2) 准备 Secret
cd d:/workspace/github-ai/idm
cp deploy/k8s/overlays/acs/secrets.yaml.example deploy/k8s/overlays/acs/secrets.yaml
# 编辑替换 REPLACE_ME

# 3) 一键部署 (30s/cmd, 不阻塞)
bash deploy/aliyun/deploy.sh

# 4) 跳过镜像构建
bash deploy/aliyun/deploy.sh --skip-build

# 5) 回滚
bash deploy/aliyun/rollback.sh
```

---

## 故障排查

| 现象 | 排查 |
| --- | --- |
| `kubectl: command not found` | `winget install Kubernetes.kubectl` (Windows) |
| `connection refused` | 检查 `KUBECONFIG` 是否正确 |
| ALB 不出现 | ACS 控制台 → 组件管理 → 装 `ack-alb-ingress` |
| Pod 拉不到镜像 | ACR 镜像仓库 + `imagePullSecrets` (见 README §3.2) |
| Secret 字段错 | 阿里云 KMS / Secrets Manager 配置 |
