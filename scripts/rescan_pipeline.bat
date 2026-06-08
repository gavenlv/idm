@echo off
REM =============================================================================
REM rescan_pipeline.bat — Windows 版本
REM =============================================================================
REM 用法:
REM   scripts\rescan_pipeline.bat                 扫全部 6 阶段
REM   scripts\rescan_pipeline.bat --stage 5       只扫阶段 5
REM   set IDM_API=http://idm.example.com ^&^& scripts\rescan_pipeline.bat
REM =============================================================================
setlocal enabledelayedexpansion

if "%IDM_API%"=="" set IDM_API=http://localhost:8000
set STAGE=
set USE_CASE=%~dp0..\use_cases\shop-orders-mex-pipeline.yml

:parse_args
if "%1"=="" goto :end_parse
if "%1"=="--api" (
  set IDM_API=%2
  shift
  shift
  goto :parse_args
)
if "%1"=="--stage" (
  set STAGE=--stage %2
  shift
  shift
  goto :parse_args
)
if "%1"=="-h" goto :help
if "%1"=="--help" goto :help
echo Unknown arg: %1
exit /b 1

:help
echo Usage: rescan_pipeline.bat [--api URL] [--stage N]
exit /b 0

:end_parse
echo ==^> [1/3] Ping API: %IDM_API%/health/ready
curl --max-time 5 -sf "%IDM_API%/health/ready" >nul 2>&1
if errorlevel 1 (
  echo     X API not ready
  exit /b 1
)

echo ==^> [2/3] Use case: %USE_CASE%

echo ==^> [3/3] Triggering rescan...
pushd %~dp0..
set PYTHON=python
timeout /t 0 >nul
python trigger_pipeline_demo.py --api "%IDM_API%" --use-case "%USE_CASE%" %STAGE% --rescan
popd
endlocal
