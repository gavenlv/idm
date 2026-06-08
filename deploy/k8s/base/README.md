# IDM K8s 部署 (Base)

> 📌 **主文档**: [deploy/aliyun/README.md](../../aliyun/README.md) — 阿里云 ACS 完整部署
>
> 本目录是 Kustomize base, 包含所有 **可移植** 的资源。

---

## 文件清单

| 文件 | 作用 |
| --- | --- |
| `namespace.yaml` | `idm` namespace |
| `serviceaccount.yaml` | SA + RRSA 注解 |
| `api-deployment.yaml` | FastAPI 1 副本, 250m/256Mi |
| `api-service.yaml` | ClusterIP 8080 |
| `web-deployment.yaml` | nginx 1 副本, 50m/64Mi |
| `web-service.yaml` | ClusterIP 80 |
| `api-config.yaml` | 非敏感 ConfigMap |
| `web-config.yaml` | nginx.conf (含 /api 反代) |
| `api-hpa.yaml` | API 1~4 副本 HPA, Web 1~2 副本 |
| `pdb.yaml` | PodDisruptionBudget |
| `network-policies.yaml` | 出口白名单 |
| `ingress-class.yaml` | alb / nginx IngressClass |
| `kustomization.yaml` | base kustomize |

## 直接使用 base

```bash
kubectl apply -k deploy/k8s/base
```

但推荐使用 **overlay** (`overlays/acs/`) 走 ACS 优化。

## Secret

**不要把 Secret 写在 base 里**。请用 overlays/<env>/secrets*.yaml 创建。
推荐用 **阿里云 KMS / Secrets Manager** (见 [overlays/acs/secrets-kms.yaml.example](../overlays/acs/secrets-kms.yaml.example))。
