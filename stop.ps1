#Requires -Version 5.1
<#
  stop.ps1 - shut down the critical-info stack (uvicorn + ngrok + n8n container).

  Usage:  .\stop.ps1            (stop the runtime pieces)
          .\stop.ps1 -KeepN8n   (leave the n8n container running)
#>

[CmdletBinding()]
param([switch]$KeepN8n)

function Step($msg) { Write-Host ('==> ' + $msg) -ForegroundColor Cyan }
function Ok($msg)   { Write-Host ('    [ok] ' + $msg) -ForegroundColor Green }
function Warn($msg) { Write-Host ('    [!] ' + $msg) -ForegroundColor Yellow }

Step 'Stopping uvicorn'
$api = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
       Where-Object { $_.CommandLine -like '*toolbox_api*' }
if ($api) {
    foreach ($p in $api) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Ok ('stopped ' + @($api).Count + ' toolbox_api process(es)')
} else {
    Ok 'toolbox_api was not running'
}

Step 'Stopping ngrok'
$ng = Get-Process ngrok -ErrorAction SilentlyContinue
if ($ng) {
    $ng | Stop-Process -Force -ErrorAction SilentlyContinue
    Ok ('stopped ' + @($ng).Count + ' ngrok process(es)')
} else {
    Ok 'ngrok was not running'
}

if ($KeepN8n) {
    Step 'n8n container'
    Warn 'left running (-KeepN8n)'
} else {
    Step 'Stopping n8n container'
    $names = @(docker ps --filter 'name=n8n' --format '{{.Names}}' 2>$null)
    $targets = $names | Where-Object { $_ -eq 'n8n-1' }
    if (-not $targets) { $targets = $names }
    if ($targets) {
        foreach ($c in $targets) { $null = docker stop $c 2>$null; Ok ('stopped ' + $c) }
    } else {
        Ok 'no running n8n container found'
    }
}

Write-Host ''
Write-Host '  Stopped. Run .\start.ps1 to bring it back.' -ForegroundColor Green
Write-Host ''
