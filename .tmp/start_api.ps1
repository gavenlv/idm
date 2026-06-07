$env:PYTHONPATH = "d:\workspace\github-ai\idm\apps\api\src;d:\workspace\github-ai\idm\packages\kg\src"
$env:APP_ENV = "local"
Set-Location d:\workspace\github-ai\idm
$proc = Start-Process -FilePath "d:\workspace\github-ai\idm\.venv\Scripts\uvicorn.exe" `
  -ArgumentList "idm_api.main:app","--host","127.0.0.1","--port","8080","--log-level","info" `
  -WorkingDirectory "d:\workspace\github-ai\idm" `
  -RedirectStandardOutput "d:\workspace\github-ai\idm\.tmp\api.log" `
  -RedirectStandardError "d:\workspace\github-ai\idm\.tmp\api.err" `
  -PassThru -UseNewEnvironment:$false
Write-Host "PID=$($proc.Id)"
