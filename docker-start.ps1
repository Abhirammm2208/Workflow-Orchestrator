# =============================================================================
# docker-start.ps1
# One-command Docker startup for the Workflow Orchestrator.
#
# Usage (from e:\Workflow):
#   .\docker-start.ps1              # full build + start
#   .\docker-start.ps1 -SkipBuild   # start without rebuilding (faster)
#   .\docker-start.ps1 -Down        # stop and remove containers
# =============================================================================

param(
    [switch]$SkipBuild,
    [switch]$Down
)

$ErrorActionPreference = "Stop"

# Colours
function Write-Step  { param($msg) Write-Host "`n  --> $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "  [OK] $msg"  -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  [!!] $msg"  -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "  [X]  $msg"  -ForegroundColor Red }

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Workflow Orchestrator — Docker Startup  " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# ---- Tear down ---------------------------------------------------------------
if ($Down) {
    Write-Step "Stopping and removing containers..."
    docker compose down
    Write-Ok "All containers stopped."
    exit 0
}

# ---- Pre-flight checks -------------------------------------------------------
Write-Step "Checking Docker is running..."
try {
    docker info | Out-Null
    Write-Ok "Docker is running."
} catch {
    Write-Fail "Docker is not running. Start Docker Desktop first."
    exit 1
}

# ---- Check .env exists -------------------------------------------------------
Write-Step "Checking .env file..."
if (-not (Test-Path ".env")) {
    Write-Warn ".env not found — copying from .env.example"
    Copy-Item ".env.example" ".env"
    Write-Warn "Edit .env and set NVIDIA_API_KEY before running workflows."
    Write-Warn "Or set USE_MOCK_LLM=true to run without an API key."
}
Write-Ok ".env exists."

# ---- Build + Start -----------------------------------------------------------
if ($SkipBuild) {
    Write-Step "Starting containers (no rebuild)..."
    docker compose up -d
} else {
    Write-Step "Building images and starting containers..."
    docker compose up --build -d
}

# ---- Wait for app health -----------------------------------------------------
Write-Step "Waiting for the API server to become healthy..."
$maxWait = 60
$waited  = 0
$healthy = $false

while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 3
    $waited += 3
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 3 -ErrorAction Stop
        if ($resp.status -eq "healthy") {
            $healthy = $true
            break
        }
    } catch {
        # Not ready yet
    }
    Write-Host "  ... waiting ($waited/$maxWait s)" -ForegroundColor DarkGray
}

if (-not $healthy) {
    Write-Warn "API did not become healthy within $maxWait seconds."
    Write-Warn "Check logs with: docker compose logs -f app"
} else {
    Write-Ok "API is healthy!"
}

# ---- Wait for db-init to finish ----------------------------------------------
Write-Step "Waiting for db-init to complete (tables + seed)..."
$waited = 0
while ($waited -lt 60) {
    Start-Sleep -Seconds 2
    $waited += 2
    $state = docker inspect --format='{{.State.Status}}' workflow_db_init 2>$null
    if ($state -eq "exited") {
        $exit_code = docker inspect --format='{{.State.ExitCode}}' workflow_db_init
        if ($exit_code -eq "0") {
            Write-Ok "db-init completed successfully."
        } else {
            Write-Warn "db-init exited with code $exit_code — check logs:"
            Write-Warn "  docker compose logs db-init"
        }
        break
    }
    Write-Host "  ... db-init still running ($waited s)" -ForegroundColor DarkGray
}

# ---- Summary -----------------------------------------------------------------
Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "  All services are up!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  API Server   :  http://localhost:8000"         -ForegroundColor White
Write-Host "  Swagger UI   :  http://localhost:8000/docs"    -ForegroundColor White
Write-Host "  Health Check :  http://localhost:8000/health"  -ForegroundColor White
Write-Host "  PostgreSQL   :  localhost:5432 / DB: Workflow" -ForegroundColor White
Write-Host "                  User: postgres  Pass: Abhiram@123" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Run the interactive CLI demo:" -ForegroundColor Yellow
Write-Host "    py demo_cli.py" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Run automated evaluation:" -ForegroundColor Yellow
Write-Host "    py evaluate.py" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Tail app logs:"   -ForegroundColor DarkGray
Write-Host "    docker compose logs -f app" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Stop everything:" -ForegroundColor DarkGray
Write-Host "    .\docker-start.ps1 -Down" -ForegroundColor DarkGray
Write-Host ""
