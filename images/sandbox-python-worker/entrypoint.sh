#!/bin/bash
# Entrypoint for Python Worker sandbox
# Restores snapshot if URL provided, then keeps container alive

# Restore snapshot if URL provided (set by Sandbox.create(snapshot_id=...))
if [ -n "$SANDBOX_SNAPSHOT_URL" ]; then
    echo "Restoring snapshot..."
    if curl -sSfL "$SANDBOX_SNAPSHOT_URL" | tar -xzf - -C /; then
        echo "Snapshot restored successfully."
    else
        echo "WARNING: Snapshot restore failed (exit code $?). Continuing without snapshot."
    fi
fi

exec tail -f /dev/null
