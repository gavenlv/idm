#!/usr/bin/env bash
# 本地端口转发调试 IDM (不需 ALB / EIP)
# 用法: bash deploy/aliyun/port-forward.sh

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-$ROOT_DIR/.kubeconfig}"

echo "==== IDM 本地调试端口转发 ===="
echo ""
echo "API:    http://localhost:8080   (原: http://localhost:8080/api/v1)"
echo "Web:    http://localhost:5173"
echo "Postgres: localhost:55432  (如果启了 port-forward)"
echo ""
echo "Ctrl+C 停止"
echo ""

# 后台起两个端口转发
kubectl port-forward -n idm svc/idm-api 8080:8080 &
PID1=$!
kubectl port-forward -n idm svc/idm-web 5173:80 &
PID2=$!

trap "kill $PID1 $PID2 2>/dev/null || true" EXIT INT TERM
wait
