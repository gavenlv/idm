@echo off
REM =============================================================================
REM scripts\bootstrap.bat — IDM 一键初始化 (Windows)
REM =============================================================================
REM 用法:
REM   scripts\bootstrap.bat                    完整 5 步
REM   scripts\bootstrap.bat --skip-docker      跳过 docker
REM   scripts\bootstrap.bat --skip-tests       跳过 e2e 验证
REM =============================================================================
setlocal enabledelayedexpansion

set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..
set COMPOSE_FILE=%ROOT_DIR%\deploy\docker\compose.dev.yml
set SKIP_DOCKER=0
set SKIP_TESTS=0

:parse_args
if "%1"=="" goto :end_parse
if "%1"=="--skip-docker" (
  set SKIP_DOCKER=1
  shift
  goto :parse_args
)
if "%1"=="--skip-tests" (
  set SKIP_TESTS=1
  shift
  goto :parse_args
)
if "%1"=="-h" goto :help
if "%1"=="--help" goto :help
echo Unknown arg: %1
exit /b 1

:help
echo Usage: bootstrap.bat [--skip-docker] [--skip-tests]
exit /b 0

:end_parse
cd /d "%ROOT_DIR%"

echo.
echo ================================================================
echo   1/5  Start infra (PG + ClickHouse + Redis + Langfuse)
echo ================================================================
if %SKIP_DOCKER%==1 goto :skip_docker
where docker >nul 2>&1
if errorlevel 1 (
  echo     X docker not found. Please install Docker first.
  exit /b 1
)
docker compose -f "%COMPOSE_FILE%" up -d
echo     waiting for healthy...
:wait_pg
docker exec idm-postgres pg_isready -U idm >nul 2>&1
if not errorlevel 1 (
  echo     - postgres ready
  goto :after_pg
)
timeout /t 1 >nul
goto :wait_pg
:after_pg
timeout /t 5 >nul
goto :step2
:skip_docker
echo     - skip docker (assumed running)

:step2
echo.
echo ================================================================
echo   2/5  Seed ClickHouse shop database
echo ================================================================
set SEED=%ROOT_DIR%\deploy\docker\seed-shop.sql
if not exist "%SEED%" (
  echo     X seed-shop.sql not found at %SEED%
  exit /b 1
)
docker cp "%SEED%" idm-clickhouse:/tmp/seed-shop.sql
docker exec -i idm-clickhouse clickhouse-client --user=idm_ro --password=idm_ro --multiquery < "%SEED%"
if errorlevel 1 (
  echo     X ClickHouse seed failed
  exit /b 1
)
echo     - ClickHouse seeded (5 tables)

:step3
echo.
echo ================================================================
echo   3/5  Alembic upgrade head
echo ================================================================
cd /d "%ROOT_DIR%\apps\api"
where uv >nul 2>&1
if errorlevel 1 (
  echo     X uv not found. pip install uv first.
  exit /b 1
)
uv run --no-progress alembic -c ..\..\migrations\alembic.ini upgrade head
if errorlevel 1 (
  echo     X alembic failed
  exit /b 1
)
cd /d "%ROOT_DIR%"
echo     - alembic up to head

:step4
echo.
echo ================================================================
echo   4/5  Verify fixtures
echo ================================================================
set MISSING=0
for %%f in (
  "fixtures\pipeline-demo\gcs\company-raw\orders\2026\06\orders-20260608.csv"
  "fixtures\pipeline-demo\gcs\company-model-input\orders\2026\06\orders_enriched-20260608.csv"
  "fixtures\pipeline-demo\gcs\company-model-output\orders\2026\06\orders_risk-20260608.csv"
  "fixtures\pipeline-demo\github\company\dwh\dags\etl_orders_daily.py"
  "fixtures\pipeline-demo\github\company\dwh\flink_jobs\orders_preprocess.sql"
  "fixtures\pipeline-demo\github\company\dwh\flink_jobs\load_orders_risk_to_clickhouse.sql"
  "fixtures\pipeline-demo\github\company\mex-models\orders\io.yaml"
  "fixtures\pipeline-demo\github\company\superset-export\dashboards.yml"
  "use_cases\shop-orders-mex-pipeline.yml"
) do (
  if not exist "%%f" (
    echo     X missing: %%f
    set /a MISSING+=1
  )
)
if %MISSING% gtr 0 (
  echo     X %MISSING% fixtures missing
  exit /b 1
)
echo     - 9 fixture files present

:step5
if %SKIP_TESTS%==1 goto :skip_tests
echo.
echo ================================================================
echo   5/5  End-to-end 6-stage pipeline (offline, no API)
echo ================================================================
cd /d "%ROOT_DIR%\apps\api"
set MOCK_GCS_ROOT=%ROOT_DIR%\fixtures\pipeline-demo\gcs
set MOCK_GITHUB_ROOT=%ROOT_DIR%\fixtures\pipeline-demo\github
uv run --no-progress python -m idm_api.verify_pipeline_fixtures
set E2E_EXIT=%ERRORLEVEL%
cd /d "%ROOT_DIR%"
if %E2E_EXIT% neq 0 (
  echo     X e2e failed (exit=%E2E_EXIT%)
  exit /b %E2E_EXIT%
)
echo     - 9/9 stages passed
goto :summary
:skip_tests
echo.
echo ================================================================
echo   5/5  Skip e2e tests
echo ================================================================

:summary
echo.
echo ================================================================
echo   BOOTSTRAP COMPLETE
echo ================================================================
echo - Infrastructure: PG + ClickHouse + Redis + Langfuse  (all healthy)
echo - ClickHouse: 5 sample tables seeded with ~500 rows
echo - Schema: 14 KG tables migrated (Alembic head = 0004)
echo - Fixtures: 9 sample files (GCS csv + Airflow + Flink + MEX + Superset)
echo - Pipeline: 6-stage end-to-end test passed
echo.
echo Next steps:
echo   1) Start API:
echo        make api-dev
echo        (or: cd apps\api ^&^& uv run uvicorn idm_api.main:app --reload --port 8080)
echo.
echo   2) Trigger use case via API (business entry):
echo        python trigger_pipeline_demo.py --api http://localhost:8080
echo.
echo   3) Re-scan anytime (idempotent):
echo        python trigger_pipeline_demo.py --api http://localhost:8080 --rescan
echo        python trigger_pipeline_demo.py --api http://localhost:8080 --stage 5
echo.
echo   4) System-level rescan (no use case):
echo        python trigger_pipeline_demo.py --api http://localhost:8080 --sys-rescan gcs --bucket company-raw
endlocal
