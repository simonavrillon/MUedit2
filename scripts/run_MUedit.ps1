#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RootDir     = Split-Path $PSScriptRoot -Parent
$BackendDir  = Join-Path $RootDir 'python'
$FrontendDir = Join-Path $RootDir 'frontend'

$env:PYTHONPATH           = "$BackendDir\src" + $(if ($env:PYTHONPATH) { ";$env:PYTHONPATH" } else { '' })
$env:MUEDIT_HOST          = if ($env:MUEDIT_HOST) { $env:MUEDIT_HOST } else { '0.0.0.0' }
$env:MUEDIT_PORT          = if ($env:MUEDIT_BACKEND_PORT) { $env:MUEDIT_BACKEND_PORT } else { '8000' }
$env:MUEDIT_FRONTEND_PORT = if ($env:MUEDIT_FRONTEND_PORT) { $env:MUEDIT_FRONTEND_PORT } else { '8080' }
$env:MUEDIT_OPEN_BROWSER  = if ($env:MUEDIT_OPEN_BROWSER) { $env:MUEDIT_OPEN_BROWSER } else { '1' }

$BackendJob = Start-Job -ScriptBlock {
    param($dir, $pythonpath)
    $env:PYTHONPATH = $pythonpath
    Set-Location $dir
    python -m muedit.cli api
} -ArgumentList $BackendDir, $env:PYTHONPATH
Write-Host "Backend started (Job $($BackendJob.Id)) on :$($env:MUEDIT_PORT)"

$FrontendJob = Start-Job -ScriptBlock {
    param($dir, $port)
    Set-Location $dir
    python -m http.server $port
} -ArgumentList $FrontendDir, $env:MUEDIT_FRONTEND_PORT
Write-Host "Frontend started (Job $($FrontendJob.Id)) on :$($env:MUEDIT_FRONTEND_PORT)"

if ($env:MUEDIT_OPEN_BROWSER -eq '1') {
    $backendPort  = $env:MUEDIT_PORT
    $frontendPort = $env:MUEDIT_FRONTEND_PORT
    $deadline = (Get-Date).AddSeconds(60)

    foreach ($check in @(
        @{ Url = "http://localhost:$backendPort/api/v1/health"; Label = "backend"  },
        @{ Url = "http://localhost:$frontendPort/";             Label = "frontend" }
    )) {
        while ((Get-Date) -lt $deadline) {
            try {
                $r = Invoke-WebRequest -Uri $check.Url -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
                if ($r.StatusCode -eq 200) { break }
            } catch {}
            Start-Sleep -Milliseconds 500
        }
        if ((Get-Date) -ge $deadline) {
            Write-Warning "Timed out waiting for $($check.Label)"
        }
    }

    try { Start-Process "http://localhost:$frontendPort/" }
    catch { Write-Warning "Could not open browser automatically: $_" }
}

try {
    while ($BackendJob.State -eq 'Running' -or $FrontendJob.State -eq 'Running') {
        Receive-Job $BackendJob, $FrontendJob -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
} finally {
    Write-Host "Stopping services..."
    Stop-Job  $BackendJob, $FrontendJob -ErrorAction SilentlyContinue
    Remove-Job $BackendJob, $FrontendJob -ErrorAction SilentlyContinue
}
