#!/bin/bash
# 初始化 PG: 安装 AGE + pgvector + pgcrypto + pg_trgm 扩展
set -e

# pgvector 已自带在 pgvector/pgvector 镜像
# 需手动装 AGE
apt-get update -qq
apt-get install -y -qq postgresql-16-age 2>/dev/null || {
    # 备用: 用 apt 源添加 (这里简化为跳过, AGE 由应用初始化)
    echo "WARN: AGE 未预装, 应用启动时会尝试 CREATE EXTENSION"
}

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<-EOSQL
    -- 必需扩展
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE EXTENSION IF NOT EXISTS vector;
    -- AGE 图查询
    CREATE EXTENSION IF NOT EXISTS age;
    -- 加载 AGE 到当前 session (生产需在 postgresql.conf 配 shared_preload_libraries)
    LOAD 'age';
    SET search_path = ag_catalog, "\$user", public;
EOSQL

echo "PG 扩展初始化完成"
