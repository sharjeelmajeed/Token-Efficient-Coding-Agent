# Starts the local Bifrost gateway as used for the WS2 live evidence (docs/HANDOFF_WS1_WS2.md).
# Usage, from the repository root:
#   powershell -ExecutionPolicy Bypass -File infra\bifrost\start-bifrost.ps1 -DataDir D:\bifrost-data
# Needs Docker Desktop running. Ollama must be running for confidential (local) requests.
# DataDir holds config.json (copied from config.example.json, with real virtual keys) and
# Bifrost's own databases. Keep it outside the repository: it contains secrets and logs.

param(
    [Parameter(Mandatory = $true)][string]$DataDir,
    [string]$EnvFile = (Join-Path $PSScriptRoot '..\..\.env')
)

$ErrorActionPreference = 'Stop'
$image = 'maximhq/bifrost@sha256:b5258c5f11c79064fc8280e49513f54c0b1da81c78bd11d3b4c1f62f8926d1ab'  # v2.5.0
$DataDir = (Resolve-Path $DataDir).Path
if (-not (Test-Path (Join-Path $DataDir 'config.json'))) {
    throw "Copy infra\bifrost\config.example.json to $DataDir\config.json and fill in the virtual keys first"
}

# Pass only the variables Bifrost needs, never the whole .env.
foreach ($name in 'GEMINI_API_KEY', 'GROQ_API_KEY', 'BIFROST_SETUP_TOKEN') {
    $line = Get-Content $EnvFile | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if (-not $line) { throw "$name is missing from $EnvFile" }
    Set-Item "Env:$name" ($line -split '=', 2)[1].Trim()
}

docker rm -f bifrost-local 2>$null | Out-Null
# --add-host forces IPv4 for host.docker.internal; Ollama only listens on IPv4.
# The databases in DataDir are kept, so the dashboard admin account survives restarts.
docker run -d --name bifrost-local `
    --add-host=host.docker.internal:host-gateway `
    -e GEMINI_API_KEY -e GROQ_API_KEY -e BIFROST_SETUP_TOKEN `
    -p 127.0.0.1:8080:8080 `
    -v "${DataDir}:/app/data" `
    $image | Out-Null
Remove-Item Env:GEMINI_API_KEY, Env:GROQ_API_KEY, Env:BIFROST_SETUP_TOKEN

for ($i = 0; $i -lt 40; $i++) {
    try {
        if ((Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/health -TimeoutSec 2).StatusCode -eq 200) {
            Write-Host 'Bifrost is running on http://127.0.0.1:8080'
            exit 0
        }
    } catch { Start-Sleep -Seconds 2 }
}
throw 'Bifrost did not become healthy; check: docker logs bifrost-local'
