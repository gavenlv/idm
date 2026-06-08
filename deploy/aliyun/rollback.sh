#!/usr/bin/env bash
# IDM 一键回滚阿里云 ACS
# 用法: bash deploy/aliyun/rollback.sh

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
KUSTOMIZE_DIR="$ROOT_DIR/deploy/k8s/overlays/acs"
TIMEOUT=30
export KUBECONFIG="${KUBECONFIG:-$ROOT_DIR/.kubeconfig}"

G="\033[0;32m"; R="\033[0;31m"; Y="\033[1;33m"; N="\033[0m"

step() { echo -e "\033[0;34m==== $* ====\033[0m"; }
ok()   { echo -e "${G}[OK]${N}  $*"; }
warn() { echo -e "${Y}[WARN]${N} $*"; }

step "[1/3] 删除 Kustomize 资源"
timeout ${TIMEOUT}s kubectl delete -k "$KUSTOMIZE_DIR" --ignore-not-found 2>&1 | tail -20
ok "kustomize 资源已删"

step "[2/3] 删除 Secret"
timeout ${TIMEOUT}s kubectl delete secret -n idm idm-api-env --ignore-not-found 2>&1
ok "Secret 已删"

step "[3/3] 删除 namespace"
timeout ${TIMEOUT}s kubectl delete namespace idm --ignore-not-found 2>&1
ok "namespace 已删"

ok "==== 回滚完成 ===="
