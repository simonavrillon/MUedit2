#Requires -Version 5.1
# Starts MUedit from a terminal (Windows) and opens it in the browser; quit with Ctrl+C.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $env:MUEDIT_OPEN_BROWSER) { $env:MUEDIT_OPEN_BROWSER = '1' }
uv run --project (Split-Path $PSScriptRoot -Parent) muedit api
exit $LASTEXITCODE
