#Requires -Version 7
<#
.SYNOPSIS
    Holdfast dev tasks for Windows (PowerShell 7). Mirrors the Makefile.
.EXAMPLE
    ./scripts/dev.ps1 up
    ./scripts/dev.ps1 logs api
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'up', 'up-prod', 'down', 'logs', 'ps', 'test', 'lint', 'fmt', 'migrate',
        'seed', 'keys', 'check', 'health', 'ports')]
    [string]$Target = 'help',

    [Parameter(Position = 1)]
    [string]$Service
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Compose = @('compose', '-f', 'docker-compose.yml', '-f', 'docker-compose.dev.yml')
$ComposeProd = @('compose', '-f', 'docker-compose.yml', '-f', 'infra/docker-compose.prod.yml')
$BackendUid = '10001'  # backend image user; it owns ./data/documents
$Python = if (Get-Command python3 -ErrorAction SilentlyContinue) { 'python3' } else { 'python' }

$Help = [ordered]@{
    'help'    = 'Show this help'
    'up'      = 'Start the dev stack (hot reload) behind https://localhost'
    'up-prod' = 'Start the prod stack (DNS-01 cert, binds TAILSCALE_BIND_IP)'
    'down'    = 'Stop the stack (keeps volumes)'
    'logs'    = 'Follow logs (dev.ps1 logs api for one service)'
    'ps'      = 'List running services'
    'test'    = 'Backend pytest (needs up for the db) + web vitest'
    'lint'    = 'ruff + ruff format --check + mypy, eslint + tsc'
    'fmt'     = 'Format backend (ruff) and web (format script, if any)'
    'migrate' = 'alembic upgrade head inside the api container'
    'seed'    = 'Seed Plaid Sandbox data (placeholder until Phase 2)'
    'keys'    = 'Create .env from .env.example with fresh secrets (refuses to overwrite)'
    'check'   = 'lint + test'
    'health'  = 'GET /api/health through Caddy'
    'ports'   = 'Assert no compose service publishes beyond the rules'
}

function Assert-Env {
    if (-not (Test-Path '.env')) {
        Write-Error "error: .env not found. Run './scripts/dev.ps1 keys' to create it from .env.example."
    }
}

function Initialize-DocsDir([string[]]$ComposeArgs) {
    New-Item -ItemType Directory -Force -Path 'data/documents' | Out-Null
    docker @ComposeArgs run --rm --no-deps --user 0 --entrypoint chown api -R "${BackendUid}:${BackendUid}" /data/documents
}

function Invoke-In([string]$Dir, [scriptblock]$Block) {
    Push-Location $Dir
    try { & $Block } finally { Pop-Location }
}

function Invoke-Lint {
    if (Test-Path 'backend/pyproject.toml') {
        Invoke-In 'backend' { uv run ruff check .; uv run ruff format --check .; uv run mypy app }
    } else { Write-Host 'skip backend: no backend/pyproject.toml' }
    if (Test-Path 'web/package.json') {
        Invoke-In 'web' { pnpm lint; pnpm typecheck }
    } else { Write-Host 'skip web: no web/package.json' }
}

function Invoke-Test {
    if (Test-Path 'backend/pyproject.toml') {
        Invoke-In 'backend' { uv run pytest -q }
    } else { Write-Host 'skip backend: no backend/pyproject.toml' }
    if (Test-Path 'web/package.json') {
        Invoke-In 'web' { pnpm test --run }
    } else { Write-Host 'skip web: no web/package.json' }
}

switch ($Target) {
    'help' {
        Write-Host 'Holdfast dev.ps1 targets:'
        foreach ($e in $Help.GetEnumerator()) { Write-Host ("  {0,-9} {1}" -f $e.Key, $e.Value) }
    }
    'up' {
        Assert-Env
        Initialize-DocsDir $Compose
        docker @Compose up -d --build --renew-anon-volumes
    }
    'up-prod' {
        Assert-Env
        docker @ComposeProd build api
        Initialize-DocsDir $ComposeProd
        docker @ComposeProd up -d --build
    }
    'down' { docker @Compose down --remove-orphans }
    'logs' {
        $logArgs = @('logs', '-f', '--tail=200')
        if ($Service) { $logArgs += $Service }
        docker @Compose @logArgs
    }
    'ps' { docker @Compose ps }
    'test' { Invoke-Test }
    'lint' { Invoke-Lint }
    'fmt' {
        if (Test-Path 'backend/pyproject.toml') {
            Invoke-In 'backend' { uv run ruff format .; uv run ruff check --fix . }
        }
        if (Test-Path 'web/package.json') {
            Invoke-In 'web' { pnpm run --if-present format }
        }
    }
    'migrate' { docker @Compose exec api alembic upgrade head }
    'seed' { & $Python scripts/seed_sandbox.py }
    'keys' { & $Python scripts/gen-keys.py --write }
    'check' { Invoke-Lint; Invoke-Test }
    'health' { curl -fsSk https://localhost/api/health; Write-Host '' }
    'ports' {
        Assert-Env
        docker @Compose config --format json | & $Python scripts/check-compose-ports.py --dev
        docker compose -f docker-compose.yml config --format json | & $Python scripts/check-compose-ports.py
    }
}
