#!/bin/bash
# Entrypoint for Node Service sandbox
# Restores snapshot if URL provided, then starts FastAPI server

# Restore snapshot if URL provided (set by Sandbox.create(snapshot_id=...))
if [ -n "$SANDBOX_SNAPSHOT_URL" ]; then
    echo "Restoring snapshot..."
    if curl -sSfL "$SANDBOX_SNAPSHOT_URL" | tar -xzf - -C /; then
        echo "Snapshot restored successfully."
    else
        echo "WARNING: Snapshot restore failed (exit code $?). Continuing without snapshot."
    fi
fi

exec python3 -m uvicorn sandbox_api.main:app --host 0.0.0.0 --port 8080
