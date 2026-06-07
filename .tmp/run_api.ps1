#!/usr/bin/env pwsh
$env:PYTHONPATH = "d:/workspace/github-ai/idm/apps/api/src;d:/workspace/github-ai/idm/packages/kg/src"
Set-Location d:/workspace/github-ai/idm
d:/workspace/github-ai/idm/.venv/Scripts/uvicorn.exe idm_api.main:app --host 127.0.0.1 --port 8080 --log-level warning
