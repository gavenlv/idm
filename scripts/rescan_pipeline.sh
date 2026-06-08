#!/usr/bin/env bash
# =============================================================================
# rescan_pipeline.sh — 重新扫描 / 增量更新 IDM 6 阶段管道
# =============================================================================
# 用法:
#   ./scripts/rescan_pipeline.sh                 # 扫全部 6 阶段
#   ./scripts/rescan_pipeline.sh --stage 5       # 只扫阶段 5
#   ./scripts/rescan_pipeline.sh --api http://idm.example.com
#
# 前置:
#   - IDM API 已起 (默认 http://localhost:8000)
#   - .env 已设 MOCK_GCS_ROOT / MOCK_GITHUB_ROOT
#   - 6 阶段 fixtures 已放好 (见 docs/design/data-pipeline-lineage.md)
# =============================================================================
set -u
shopt -s lastpipe 2>/dev/null || true

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
API_BASE="${IDM_API:-http://localhost:8000}"
STAGE=""
USE_CASE="$ROOT_DIR/use_cases/shop-orders-mex-pipeline.yml"

# === 解析参数 ===
while [[ $# -gt 0 ]]; do
  case "$1" in
    --api)     API_BASE="$2"; shift 2 ;;
    --stage)   STAGE="$2"; shift 2 ;;
    --use-case) USE_CASE="$2"; shift 2 ;;
    -h|--help) head -20 "$0" | tail -18; exit 0 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# === 探活 ===
echo "==> [1/3] Ping API: $API_BASE/health/ready"
curl --max-time 5 -sf "$API_BASE/health/ready" | head -c 200 || {
  echo "    ✗ API not ready"; exit 1; }

# === 列 source ===
echo "==> [2/3] Use case: $USE_CASE"
[ -f "$USE_CASE" ] || { echo "    ✗ use case not found"; exit 1; }

# === 跑 ===
echo "==> [3/3] Triggering rescan..."
cd "$ROOT_DIR"
PYBIN="${PYTHON:-python}"
# timeout 30s 包住, 失败不阻塞
timeout 30s $PYBIN trigger_pipeline_demo.py \
  --api "$API_BASE" \
  ${STAGE:+--stage "$STAGE"} \
  --use-case "$USE_CASE" \
  --rescan 2>&1 | tail -120

echo ""
echo "==> Done. Check:"
echo "    GET $API_BASE/api/v1/assets      (table_assets)"
echo "    GET $API_BASE/api/v1/skills      (skill registry)"
echo "    GET $API_BASE/api/v1/suggestions (pending AI suggestions)"
