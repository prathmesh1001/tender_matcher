# Start the run worker on this machine (see docker-compose.host-worker.yml).
# Uses backend/.env (database on localhost:5433, data in ../data) and the Redis queue in Docker.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\backend")
$env:JOB_QUEUE_BACKEND = "rq"
$env:REDIS_URL = "redis://localhost:6379/0"
& .\.venv\Scripts\python.exe -m app.workers.worker
