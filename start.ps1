#Requires -Version 5.1
<#
  start.ps1 - bring the whole critical-info stack up in one command.

  Usage:  .\start.ps1            (normal run)
          .\start.ps1 -Status    (report what is up, change nothing)
          .\start.ps1 -SkipNgrok (no tunnel)

  Steps handled automatically:
    1. Docker Desktop engine   (waits for the daemon to answer)
    2. n8n container           (start, wait for HTTP on 5678)
    3. uvicorn toolbox_api     (start, wait for the port)
    4. ngrok tunnel to n8n     (optional)
    5. n8n workflow ACTIVE     (via public REST API, no UI clicking)

  uvicorn and ngrok run in separate minimized PowerShell windows so this
  script can finish. Close those windows to shut them down.
#>

[CmdletBinding()]
param(
    [switch]$Status,
    [switch]$SkipNgrok
)

$ErrorActionPreference = 'Stop'
$ProjectDir    = $PSScriptRoot
$WorkflowId    = 'UyBdrlVE2KsPJpiM'
$WorkflowName  = 'Critical Info Toolbox (MCP)'
$N8nPort       = 5678
$ApiPort       = 8000
$NgrokDomain   = 'half-unless-donor.ngrok-free.dev'

function Step($msg) { Write-Host ('==> ' + $msg) -ForegroundColor Cyan }
function Ok($msg)   { Write-Host ('    [ok] ' + $msg) -ForegroundColor Green }
function Warn($msg) { Write-Host ('    [!] ' + $msg) -ForegroundColor Yellow }
function Die($msg)  { Write-Host ('    [X] ' + $msg) -ForegroundColor Red; exit 1 }

function Get-EnvValue([string]$name, [string]$fallback = '') {
    $envFile = Join-Path $ProjectDir '.env'
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -like ($name + '=*') } | Select-Object -First 1
        if ($line) { return $line.Substring($name.Length + 1).Trim() }
    }
    return $fallback
}

function Test-Port([int]$port) {
    try {
        $t = Test-NetConnection -ComputerName '127.0.0.1' -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue
        return [bool]$t
    } catch { return $false }
}

# Launches a minimized PowerShell window running a command. Path is single-quoted
# for the inner shell, so the project dir may safely contain spaces.
function Launch-Window([string]$label, [string]$innerCommand) {
    $cmd = 'Set-Location -LiteralPath ''' + $ProjectDir.Replace("'", "''") + '''; ' + $innerCommand
    Start-Process powershell -ArgumentList @('-NoExit', '-Command', $cmd) -WindowStyle Minimized
    Write-Host ('    launched: ' + $label) -ForegroundColor DarkGray
}

if ($Status) {
    Step 'Status'
    $null = docker info 2>$null
    if ($LASTEXITCODE -eq 0) { Ok 'Docker engine: running' } else { Warn 'Docker engine: DOWN' }
    $checks = @(
        @('n8n container', $N8nPort),
        @('uvicorn toolbox_api', $ApiPort),
        @('ngrok agent api', 4040)
    )
    foreach ($c in $checks) {
        if (Test-Port $c[1]) { Ok ($c[0] + ': up on ' + $c[1]) }
        else { Warn ($c[0] + ': not listening on ' + $c[1]) }
    }
    exit 0
}

Write-Host ''
Write-Host '  Critical Information - starting stack' -ForegroundColor White
Write-Host '  -------------------------------------' -ForegroundColor DarkGray

# ------------------------------------------------------ 1. docker engine up
Step 'Docker engine'
$dockerUp = $false
try { $null = docker info 2>$null; if ($LASTEXITCODE -eq 0) { $dockerUp = $true } } catch {}

if (-not $dockerUp) {
    Warn 'Docker engine is not running - launching Docker Desktop'
    $desktop = @(
        (Join-Path $Env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path ${Env:ProgramFiles(x86)} 'Docker\Docker\Docker Desktop.exe')
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $desktop) {
        Die 'Could not find Docker Desktop.exe - start it manually and re-run this script'
    }
    Start-Process $desktop

    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        try { $null = docker info 2>$null; if ($LASTEXITCODE -eq 0) { $dockerUp = $true; break } } catch {}
        Write-Host '    waiting for the engine...' -ForegroundColor DarkGray
    }
    if (-not $dockerUp) { Die 'Docker engine did not come up within 3 minutes' }
}
Ok 'Docker engine is running'

# ------------------------------------------------------ 2. n8n container up
Step 'n8n container'
$allNames = @(docker ps -a --filter 'name=n8n' --format '{{.Names}}' 2>$null)
# Main container is the one publishing 5678; fall back to a name match.
$container = $allNames | Where-Object { docker port $_ 2>$null } |
             Where-Object { (docker port $_ 2>$null) -match ':5678->' } | Select-Object -First 1
if (-not $container) { $container = $allNames | Where-Object { $_ -match '(^|-)n8n-1$' } | Select-Object -First 1 }
if (-not $container) { $container = $allNames | Select-Object -First 1 }
if (-not $container) {
    Die 'No n8n container found - run docker compose up -d in your n8n folder first'
}

# Start in dependency-friendly order: main n8n first, then the rest of the project.
$rest = @($allNames | Where-Object { $_ -ne $container })
$toStart = @($container) + $rest

foreach ($c in $toStart) {
    $state = docker inspect -f '{{.State.Status}}' $c 2>$null
    if ($state -eq 'running') { Ok ($c + ' already running') }
    else {
        Write-Host ('    starting ' + $c + ' (was: ' + $state + ')')
        $null = docker start $c 2>$null
    }
}

$deadline = (Get-Date).AddMinutes(2)
$up = $false
while ((Get-Date) -lt $deadline) {
    if ((docker inspect -f '{{.State.Running}}' $container 2>$null) -eq 'true') { $up = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $up) { Die ('Container ' + $container + ' did not reach running state') }
Ok ('container ' + $container + ' is up')

$deadline = (Get-Date).AddMinutes(2)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $resp = Invoke-WebRequest -Uri ('http://localhost:' + $N8nPort + '/healthz') -UseBasicParsing -TimeoutSec 5
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 3
    Write-Host '    waiting for n8n HTTP...' -ForegroundColor DarkGray
}
if ($ready) { Ok ('n8n is answering on port ' + $N8nPort) }
else { Warn ('n8n not answering on port ' + $N8nPort + ' yet - it may still be booting') }

# ------------------------------------------------------------ 3. uvicorn up
Step 'toolbox API (uvicorn)'
if (Test-Port $ApiPort) {
    Ok ('already listening on port ' + $ApiPort)
} else {
    $existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*toolbox_api*' } | Select-Object -First 1
    if ($existing) {
        Ok ('uvicorn already running (PID ' + $existing.ProcessId + ')')
    } else {
        Launch-Window 'uvicorn toolbox_api' 'python -m uvicorn toolbox_api:app --host 0.0.0.0 --port 8000'
        $deadline = (Get-Date).AddSeconds(60)
        $apiUp = $false
        while ((Get-Date) -lt $deadline) {
            if (Test-Port $ApiPort) { $apiUp = $true; break }
            Start-Sleep -Seconds 2
        }
        if ($apiUp) { Ok ('uvicorn is listening on port ' + $ApiPort) }
        else { Warn ('uvicorn did not come up on port ' + $ApiPort + ' - check its window for errors') }
    }
}

# ----------------------------------------------------------- 4. ngrok tunnel
Step 'ngrok tunnel'
if ($SkipNgrok -or $Env:NGROK_SKIP -eq '1') {
    Warn 'skipped by request'
} else {
    $runningNgrok = Get-Process ngrok -ErrorAction SilentlyContinue
    if ($runningNgrok) {
        Ok ('ngrok already running (PID ' + $runningNgrok[0].Id + ')')
    } else {
        $ngrok = (Get-Command ngrok -ErrorAction SilentlyContinue).Source
        if (-not $ngrok) {
            Warn 'ngrok is not on PATH - skipping tunnel, tools will be unreachable from OpenCode'
        } else {
            Launch-Window 'ngrok' ('ngrok http --url=' + $NgrokDomain + ' ' + $N8nPort)
            $deadline = (Get-Date).AddSeconds(60)
            $tunnelUp = $false
            while ((Get-Date) -lt $deadline) {
                Start-Sleep -Seconds 3
                try {
                    $t = Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 3
                    $urls = @($t.tunnels | ForEach-Object { $_.public_url })
                    if ($urls -contains ('https://' + $NgrokDomain)) { $tunnelUp = $true; break }
                } catch {}
                Write-Host '    waiting for tunnel...' -ForegroundColor DarkGray
            }
            if ($tunnelUp) { Ok ('tunnel live: ' + $NgrokDomain + ' -> port ' + $N8nPort) }
            else { Warn 'tunnel did not confirm - check http://127.0.0.1:4040' }
        }
    }
}

# -------------------------------------------------- 5. activate the workflow
Step ('n8n workflow: ' + $WorkflowName)
$apiKey  = Get-EnvValue 'N8N_API_KEY'
$baseUrl = Get-EnvValue 'N8N_BASE_URL' ('http://localhost:' + $N8nPort)

if (-not $apiKey) {
    Warn 'N8N_API_KEY missing from .env - activate the workflow in the n8n UI instead'
} else {
    $headers = @{ 'X-N8N-API-KEY' = $apiKey }
    $deadline = (Get-Date).AddSeconds(120)
    $done = $false
    while ((Get-Date) -lt $deadline -and -not $done) {
        try {
            $wf = Invoke-RestMethod -Uri ($baseUrl + '/api/v1/workflows/' + $WorkflowId) -Headers $headers -TimeoutSec 10
            if ($wf.active) {
                Ok 'workflow is already ACTIVE'
            } else {
                $null = Invoke-RestMethod -Uri ($baseUrl + '/api/v1/workflows/' + $WorkflowId) -Method Post -Headers $headers -TimeoutSec 10
                Ok 'workflow activated - listening for tool calls'
            }
            $done = $true
        } catch {
            Start-Sleep -Seconds 5
        }
    }
    if (-not $done) { Warn ('could not reach the n8n API at ' + $baseUrl + ' - activate the workflow in the UI') }
}

Write-Host ''
Write-Host '  Stack is up. Ask the agent to process a source and it will use the tools.' -ForegroundColor Green
Write-Host ''
exit 0
