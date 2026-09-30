#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RootDir    = Split-Path $PSScriptRoot -Parent
$BackendDir = Join-Path $RootDir 'python'

# `uv run` syncs the locked environment and executes inside it, so the app
# launches from a bare checkout with no activation step.  Falling back to a
# plain `python` keeps the script working inside an already-activated
# environment (conda, venv) and when uv is not installed.
if ((Get-Command uv -ErrorAction SilentlyContinue) -and ($env:MUEDIT_NO_UV -ne '1')) {
    $PyExe  = 'uv'
    $PyArgs = @('run', '--project', $RootDir, 'python')
} else {
    $PyExe  = 'python'
    $PyArgs = @()
}

$env:PYTHONPATH          = "$BackendDir\src" + $(if ($env:PYTHONPATH) { ";$env:PYTHONPATH" } else { '' })
$env:MUEDIT_HOST         = if ($env:MUEDIT_HOST) { $env:MUEDIT_HOST } else { '127.0.0.1' }
$env:MUEDIT_PORT         = if ($env:MUEDIT_BACKEND_PORT) { $env:MUEDIT_BACKEND_PORT } else { '8000' }
$env:MUEDIT_OPEN_BROWSER = if ($env:MUEDIT_OPEN_BROWSER) { $env:MUEDIT_OPEN_BROWSER } else { '1' }

# One server: the API, and the page at / on the same origin.  The unary comma
# keeps $PyArgs a single array argument instead of being unrolled into separate
# positional parameters by -ArgumentList.
$ServerJob = Start-Job -ScriptBlock {
    param($dir, $pythonpath, $exe, $pyargs)
    $env:PYTHONPATH = $pythonpath
    Set-Location $dir
    & $exe @pyargs -m muedit.cli api
} -ArgumentList $BackendDir, $env:PYTHONPATH, $PyExe, (, $PyArgs)
$Url = "http://127.0.0.1:$($env:MUEDIT_PORT)/"
Write-Host "MUedit started (Job $($ServerJob.Id)) on $Url"

if ($env:MUEDIT_OPEN_BROWSER -eq '1') {
    $deadline = (Get-Date).AddSeconds(60)
    $ready = $false
    while (-not $ready -and (Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "${Url}api/v1/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
            $ready = $r.StatusCode -eq 200
        } catch {}
        if (-not $ready) { Start-Sleep -Milliseconds 500 }
    }
    if ($ready) {
        try { Start-Process $Url }
        catch { Write-Warning "Could not open browser automatically: $_" }
    } else {
        Write-Warning "Timed out waiting for MUedit to start"
    }
}

try {
    while ($ServerJob.State -eq 'Running') {
        Receive-Job $ServerJob -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    Receive-Job $ServerJob -ErrorAction SilentlyContinue
} finally {
    Write-Host "Stopping MUedit..."
    Stop-Job  $ServerJob -ErrorAction SilentlyContinue
    Remove-Job $ServerJob -ErrorAction SilentlyContinue
}
