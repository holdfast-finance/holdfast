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
        'seed', 'keys', 'check', 'health', 'ports', 'sync', 'wt-new', 'test-locked', 'release-check', 'dist')]
    [string]$Target = 'help',

    [Parameter(Position = 1)]
    [string]$Service,

    [Parameter(Position = 2)]
    [string]$Base = 'origin/main'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Compose = @('compose', '-f', 'docker-compose.yml', '-f', 'docker-compose.dev.yml')
$ComposeProd = @('compose', '-f', 'docker-compose.yml', '-f', 'infra/docker-compose.prod.yml')
$BackendUid = '10001'  # backend image user; it owns ./data/documents
$BackendImage = 'holdfast-backend:local'
$Python = if (Get-Command python3 -ErrorAction SilentlyContinue) { 'python3' } else { 'python' }

$Help = [ordered]@{
    'help'    = 'Show this help'
    'up'      = 'Start the dev stack (hot reload) behind https://localhost'
    'up-prod' = 'Start the prod stack (DNS-01 cert, binds TAILSCALE_BIND_IP)'
    'down'    = 'Stop the stack (keeps volumes)'
    'logs'    = 'Follow logs (dev.ps1 logs api for one service)'
    'ps'      = 'List running services'
    'test'    = 'Backend pytest (needs up for the db) + web vitest'
    'lint'    = 'ruff + ruff format --check + mypy + import-linter, eslint + tsc'
    'fmt'     = 'Format backend (ruff) and web (format script, if any)'
    'migrate' = 'alembic upgrade head as holdfast_owner (one-off migrate container)'
    'seed'    = 'Seed Plaid Sandbox data (placeholder until Phase 2)'
    'keys'    = 'Create .env from .env.example with fresh secrets (refuses to overwrite)'
    'check'   = 'lint + test'
    'health'  = 'GET /api/health through Caddy'
    'ports'   = 'Assert no compose service publishes beyond the rules'
    'sync'    = 'Fast-forward main to origin/main; elsewhere print ahead/behind'
    'wt-new'  = 'New worktree on a fresh base: dev.ps1 wt-new <branch> [base]'
    'test-locked' = 'test under an exclusive lock (${XDG_RUNTIME_DIR:-/tmp}/holdfast-make-test.lock) so parallel worktrees share the test DB safely'
    'dist' = 'Build wheel + sdist (holdfast-finance) and SHA256SUMS into ./dist. Does not publish.'
    'release-check' = 'Compare product SemVer and Python version. Does not tag or publish.'
}

function Assert-Env {
    if (-not (Test-Path '.env')) {
        Write-Error "error: .env not found. Run './scripts/dev.ps1 keys' to create it from .env.example."
    }
}

# Plain `docker run`, not `compose run`: compose services drop every capability, and chown needs
# CAP_CHOWN.
function Initialize-DocsDir([string[]]$ComposeArgs) {
    New-Item -ItemType Directory -Force -Path 'data/documents' | Out-Null
    docker @ComposeArgs build api
    $docs = Join-Path $Root 'data/documents'
    docker run --rm --user 0 --entrypoint chown -v "${docs}:/data/documents" $BackendImage -R "${BackendUid}:${BackendUid}" /data/documents
}

# Reads one value from .env without printing it.
function Get-EnvValue([string]$Name) {
    if (-not (Test-Path '.env')) { return $null }
    $line = Get-Content '.env' | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
    if ($line) { return $line.Substring($Name.Length + 1) }
    return $null
}

function Invoke-In([string]$Dir, [scriptblock]$Block) {
    Push-Location $Dir
    try { & $Block } finally { Pop-Location }
}

function Invoke-Lint {
    if (Test-Path 'backend/pyproject.toml') {
        Invoke-In 'backend' { uv run ruff check .; uv run ruff format --check .; uv run mypy app; uv run lint-imports }
    } else { Write-Host 'skip backend: no backend/pyproject.toml' }
    if (Test-Path 'web/package.json') {
        Invoke-In 'web' { pnpm lint; pnpm typecheck }
    } else { Write-Host 'skip web: no web/package.json' }
}

# Tests use a separate holdfast_test database and Redis db 15, never the dev stack's data
# (the migration test downgrades to base; API tests FLUSHDB).
# Host-side tests reach the dev stack's loopback-published db/redis: TEST_DATABASE_URL as the
# runtime role (holdfast_app), TEST_MIGRATIONS_DATABASE_URL as the schema owner (holdfast_owner).
# Pre-set any TEST_* variable to override. Variables this sets are removed afterwards.
function Invoke-Test {
    if (Test-Path 'backend/pyproject.toml') {
        $set = @()
        if (Test-Path '.env') {
            if (-not $env:TEST_DATABASE_URL) {
                $env:TEST_DATABASE_URL = "postgresql+psycopg://holdfast_app:$(Get-EnvValue 'POSTGRES_APP_PASSWORD')@127.0.0.1:5432/holdfast_test"
                $set += 'TEST_DATABASE_URL'
            }
            if (-not $env:TEST_MIGRATIONS_DATABASE_URL) {
                $env:TEST_MIGRATIONS_DATABASE_URL = "postgresql+psycopg://holdfast_owner:$(Get-EnvValue 'POSTGRES_OWNER_PASSWORD')@127.0.0.1:5432/holdfast_test"
                $set += 'TEST_MIGRATIONS_DATABASE_URL'
            }
            if (-not $env:TEST_REDIS_URL) {
                $env:TEST_REDIS_URL = 'redis://127.0.0.1:56379/15'
                $set += 'TEST_REDIS_URL'
            }
        }
        if ($set -contains 'TEST_DATABASE_URL' -and $set -contains 'TEST_MIGRATIONS_DATABASE_URL') {
            # Create holdfast_test once (idempotent) with the same role/grant script as the real db.
            docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T -e POSTGRES_DB=holdfast_test db sh -c 'createdb -U "$POSTGRES_USER" "$POSTGRES_DB" 2>/dev/null; /docker-entrypoint-initdb.d/00-roles.sh >/dev/null'
        }
        try { Invoke-In 'backend' { uv run pytest -q } }
        finally { foreach ($name in $set) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue } }
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
        Initialize-DocsDir $ComposeProd
        docker @ComposeProd up -d --build db redis
        docker @ComposeProd run --rm migrate
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
    'migrate' { Assert-Env; docker @Compose run --rm migrate }
    'seed' { & $Python scripts/seed_sandbox.py }
    'keys' { & $Python scripts/gen-keys.py --write }
    'check' { Invoke-Lint; Invoke-Test }
    'health' { curl -fsSk https://localhost/api/health; Write-Host '' }
    'ports' {
        Assert-Env
        docker @Compose config --format json | & $Python scripts/check-compose-ports.py --dev
        docker compose -f docker-compose.yml config --format json | & $Python scripts/check-compose-ports.py
        # Prod needs TAILSCALE_BIND_IP/CF_API_TOKEN: use .env values if set, else CI-style dummies.
        $envText = Get-Content .env -Raw
        $saved = @{ TAILSCALE_BIND_IP = $env:TAILSCALE_BIND_IP; CF_API_TOKEN = $env:CF_API_TOKEN }
        try {
            if ($envText -notmatch '(?m)^TAILSCALE_BIND_IP=.+') { $env:TAILSCALE_BIND_IP = '100.64.0.1' }
            if ($envText -notmatch '(?m)^CF_API_TOKEN=.+') { $env:CF_API_TOKEN = 'x' * 40 }
            docker @ComposeProd config --format json | & $Python scripts/check-compose-ports.py --prod
        } finally {
            $env:TAILSCALE_BIND_IP = $saved.TAILSCALE_BIND_IP
            $env:CF_API_TOKEN = $saved.CF_API_TOKEN
        }
    }
    'sync' {
        git fetch origin --quiet
        if ((git rev-parse --abbrev-ref HEAD) -eq 'main') { git merge --ff-only origin/main }
        else {
            $ab = (git rev-list --left-right --count 'HEAD...origin/main') -split '\s+'
            Write-Host "ahead $($ab[0]), behind $($ab[1]) vs origin/main"
        }
    }
    'wt-new' {
        if (-not $Service) { Write-Error 'usage: dev.ps1 wt-new <branch> [base]' }
        bash scripts/wt-new.sh $Service $Base
    }
    'release-check' {
        & $Python scripts/cut_release.py check
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    'dist' {
        if (Test-Path dist) { Remove-Item -Recurse -Force dist }
        Push-Location backend
        try { uv build --out-dir ../dist; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } } finally { Pop-Location }
        Remove-Item -Force dist/.gitignore -ErrorAction SilentlyContinue
        & $Python scripts/dist_checksums.py dist
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    'test-locked' {
        # Same path as `make test-locked`. FileShare.None fails at once on a sharing violation;
        # retry until HOLDFAST_TEST_LOCK_WAIT seconds (default 1800), then error.
        $lockDir = if ([string]::IsNullOrEmpty($env:XDG_RUNTIME_DIR)) { '/tmp' } else { $env:XDG_RUNTIME_DIR }
        New-Item -ItemType Directory -Force -Path $lockDir | Out-Null
        $lockPath = Join-Path $lockDir 'holdfast-make-test.lock'
        $waitSec = 1800
        if (-not [string]::IsNullOrEmpty($env:HOLDFAST_TEST_LOCK_WAIT)) {
            $parsed = 0
            if (-not [int]::TryParse($env:HOLDFAST_TEST_LOCK_WAIT, [ref]$parsed) -or $parsed -lt 0) {
                throw 'test-locked: HOLDFAST_TEST_LOCK_WAIT must be a non-negative integer number of seconds.'
            }
            $waitSec = $parsed
        }
        $lock = $null
        $started = [DateTimeOffset]::UtcNow
        while ($null -eq $lock) {
            try {
                $lock = [System.IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
            } catch {
                $ex = $_.Exception
                $isIo = $false
                while ($null -ne $ex) {
                    if ($ex -is [System.IO.IOException]) { $isIo = $true; break }
                    $ex = $ex.InnerException
                }
                if (-not $isIo) { throw }
                $elapsed = ([DateTimeOffset]::UtcNow - $started).TotalSeconds
                if ($elapsed -ge $waitSec) {
                    throw "test-locked: timed out after ${waitSec}s waiting for lock ${lockPath}"
                }
                Start-Sleep -Milliseconds 200
            }
        }
        try { Invoke-Test } finally { $lock.Dispose() }
    }
}
