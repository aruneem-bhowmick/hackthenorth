#!/bin/sh
# Run migrations before accepting traffic, then keep API and worker together so
# both see the same Railway-mounted upload directory.
set -eu

mkdir -p "${UPLOADS_DIR:-/data/uploads}"
cd /app
uv run --directory apps/api alembic upgrade head

uv run --directory apps/worker arq worker.settings.WorkerSettings &
worker_pid=$!
uv run --directory apps/api uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" &
api_pid=$!

shutdown() {
    kill -TERM "$api_pid" "$worker_pid" 2>/dev/null || true
    wait "$api_pid" "$worker_pid" 2>/dev/null || true
}
trap shutdown INT TERM

# Railway restarts the service if either essential process exits. This is safer
# for the demo than leaving a reachable API with a dead review worker.
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$worker_pid" 2>/dev/null; do
    sleep 2
done

shutdown
exit 1
