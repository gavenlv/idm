#!/usr/bin/env bash
# IDM 一键部署到阿里云 ACS
# 铁律: 任何命令 timeout 30s; 不阻塞; 自动续行
# 用法:
#   bash deploy/aliyun/deploy.sh                  # 完整流程
#   bash deploy/aliyun/deploy.sh --skip-build     # 跳过镜像构建 (已构建过)
#   bash deploy/aliyun/deploy.sh --skip-secret    # 跳过 secret (已创建)

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
KUSTOMIZE_DIR="$ROOT_DIR/deploy/k8s/overlays/acs"
LOG_DIR="$ROOT_DIR/apps/.tmp"
mkdir -p "$LOG_DIR"

SKIP_BUILD=false
SKIP_SECRET=false
for arg in "$@"; do
  case "$arg" in
    --skip-build)  SKIP_BUILD=true ;;
    --skip-secret) SKIP_SECRET=true ;;
    *) echo "[WARN] unknown arg: $arg" ;;
  esac
done

# 公共变量
KUBECONFIG="$ROOT_DIR/.kubeconfig"
ACR_NS="${ACR_NS:-your-ns}"
ACR_REGION="${ACR_REGION:-cn-hangzhou}"
ACR_REGISTRY="registry.${ACR_REGION}.aliyuncs.com"
IMAGE_TAG="${IMAGE_TAG:-0.1.0}"
TIMEOUT=30

export KUBECONFIG

# 颜色
G="\033[0;32m"; R="\033[0;31m"; Y="\033[1;33m"; B="\033[0;34m"; N="\033[0m"
ok()   { echo -e "${G}[OK]${N}  $*"; }
warn() { echo -e "${Y}[WARN]${N} $*"; }
err()  { echo -e "${R}[ERR]${N} $*"; }
step() { echo -e "${B}==== $* ====${N}"; }

# === 步骤 1: 镜像构建 (可选) ===
if [ "$SKIP_BUILD" = "false" ]; then
  step "[1/6] 构建并推送镜像 (timeout 30s/cmd, 不阻塞)"
  ts=$(date +%s)
  for img in "idm-api:apps/api/Dockerfile" "idm-web:apps/web/Dockerfile"; do
    name=${img%%:*}; df=${img##*:}
    log="$LOG_DIR/build-${name}-${ts}.log"
    err_log="$LOG_DIR/build-${name}-${ts}.err"
    cmd="docker build -t ${ACR_REGISTRY}/${ACR_NS}/${name}:${IMAGE_TAG} -f ${df} ."
    echo "  > $cmd  ->  $log"
    timeout ${TIMEOUT}s $cmd >"$log" 2>"$err_log" &
    pid=$!
    sleep ${TIMEOUT}
    if kill -0 $pid 2>/dev/null; then
      warn "build $name 仍在跑 (PID $pid), 30s 后继续 (日志 $log)"
      kill $pid 2>/dev/null || true
    else
      ok "build $name done"
    fi
  done
else
  warn "[1/6] 跳过构建 (--skip-build)"
fi

# === 步骤 2: 镜像推送 ===
step "[2/6] 推送镜像到 ACR"
ts=$(date +%s)
for name in idm-api idm-web; do
  log="$LOG_DIR/push-${name}-${ts}.log"
  timeout ${TIMEOUT}s docker push ${ACR_REGISTRY}/${ACR_NS}/${name}:${IMAGE_TAG} >"$log" 2>&1 &
  pid=$!
  sleep ${TIMEOUT}
  if kill -0 $pid 2>/dev/null; then
    warn "push $name 仍在跑, 30s 后继续 (日志 $log)"
    kill $pid 2>/dev/null || true
  else
    ok "push $name done"
  fi
done

# === 步骤 3: Kubeconfig 检查 ===
step "[3/6] 检查 Kubeconfig (timeout 10s)"
if ! timeout 10s kubectl cluster-info >/dev/null 2>&1; then
  err "kubectl 连不上集群, 请检查 .kubeconfig"
  exit 1
fi
ok "kubectl OK"
timeout 10s kubectl get nodes --no-headers | head -3

# === 步骤 4: 准备 Secret ===
step "[4/6] 准备 Secret (timeout 10s)"
if [ "$SKIP_SECRET" = "false" ]; then
  if [ ! -f "$KUSTOMIZE_DIR/secrets.yaml" ]; then
    if [ -f "$KUSTOMIZE_DIR/secrets.yaml.example" ]; then
      cp "$KUSTOMIZE_DIR/secrets.yaml.example" "$KUSTOMIZE_DIR/secrets.yaml"
      warn "已复制 secrets.yaml.example → secrets.yaml, 请编辑后重跑"
      exit 2
    else
      err "找不到 secrets 模板"
      exit 2
    fi
  fi
  timeout 10s kubectl apply -f "$KUSTOMIZE_DIR/secrets.yaml" && ok "Secret 创建成功"
else
  warn "[4/6] 跳过 secret (--skip-secret)"
fi

# === 步骤 5: Kustomize apply ===
step "[5/6] kubectl apply -k $KUSTOMIZE_DIR (timeout 30s)"
ts=$(date +%s)
log="$LOG_DIR/apply-${ts}.log"
timeout ${TIMEOUT}s kubectl apply -k "$KUSTOMIZE_DIR" >"$log" 2>&1 &
pid=$!
sleep ${TIMEOUT}
if kill -0 $pid 2>/dev/null; then
  warn "apply 仍在跑, 30s 后继续 (日志 $log)"
  kill $pid 2>/dev/null || true
else
  ok "apply done"
  tail -10 "$log"
fi

# === 步骤 6: 状态检查 ===
step "[6/6] 检查部署状态 (timeout 10s/cmd)"
for ns in idm; do
  echo "--- pods ---"
  timeout 10s kubectl get pods -n "$ns" --no-headers 2>/dev/null || warn "no namespace $ns yet"
  echo "--- svc ---"
  timeout 10s kubectl get svc -n "$ns" --no-headers 2>/dev/null
  echo "--- ingress ---"
  timeout 10s kubectl get ingress -n "$ns" --no-headers 2>/dev/null
done

ok "==== 部署流程完成 ===="
echo ""
echo " 等待 30s 让 ALB 创建完成, 然后:"
echo "   kubectl get ingress -n idm   # 拿 EIP"
echo "   curl http://<EIP>/health"
