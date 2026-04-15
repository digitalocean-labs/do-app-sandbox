#!/usr/bin/env python3
"""E2E Incremental Snapshot Chain Validation.

Tests the full incremental snapshot lifecycle:
1. Create sandbox → write files → full snapshot (base)
2. Edit files → incremental snapshot #1
3. Delete file + edit file → incremental snapshot #2
4. Create new sandbox → restore_snapshot_chain from #2 → verify ALL state

This validates: marker-based change detection, manifest diffing,
deletion tracking, chain resolution, and multi-layer restore.

Usage:
    PYTHONPATH=src:$PYTHONPATH python tests/e2e_incremental_snapshot_validation.py
"""

import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from do_app_sandbox import Sandbox, SnapshotManager
from do_app_sandbox.spaces import create_spaces_config_from_env

# ── Logging ────────────────────────────────────────────────────────────────

LOG_FILE = os.path.join(os.path.dirname(__file__), "artifacts", "incremental_snapshot_validation.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log(msg: str, level: str = "INFO"):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

# ── Cleanup tracker ───────────────────────────────────────────────────────

sandboxes_to_cleanup: list = []
snapshots_to_cleanup: list[str] = []
results: list[dict] = []

def record(name: str, passed: bool, duration: float, details: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append({"test": name, "passed": passed, "duration_s": round(duration, 1), "details": details})
    log(f"  {status}: {name} ({round(duration, 1)}s) {details}", "RESULT")


def check_env():
    required = ["DIGITALOCEAN_TOKEN", "SPACES_BUCKET", "SPACES_REGION", "SPACES_ACCESS_KEY", "SPACES_SECRET_KEY"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        log(f"Missing env vars: {', '.join(missing)}", "ERROR")
        sys.exit(1)


# ── Tests ─────────────────────────────────────────────────────────────────

def test_1_create_sandbox():
    """Create sandbox with Spaces config."""
    log("Test 1: Create sandbox")
    t0 = time.time()

    sandbox = Sandbox.create(
        image="python",
        spaces_config=create_spaces_config_from_env(),
        wait_ready=True,
        timeout=300,
    )
    sandboxes_to_cleanup.append(sandbox)
    assert sandbox.exec("echo ok").exit_code == 0

    record("create_sandbox", True, time.time() - t0, f"app_id={sandbox._app_id}")
    return sandbox


def test_2_write_initial_files(sandbox):
    """Write initial file set for the base snapshot."""
    log("Test 2: Write initial files")
    t0 = time.time()

    sandbox.exec("mkdir -p /home/sandbox/app/src /home/sandbox/app/data")
    sandbox.exec("echo 'print(\"hello\")' > /home/sandbox/app/src/main.py")
    sandbox.exec("echo 'import os' > /home/sandbox/app/src/utils.py")
    sandbox.exec("echo '{\"version\": 1}' > /home/sandbox/app/data/config.json")
    sandbox.exec("echo 'README content' > /home/sandbox/app/README.md")

    result = sandbox.exec("find /home/sandbox/app -type f | sort")
    log(f"  Initial files:\n{result.stdout}")

    record("write_initial_files", True, time.time() - t0, "4 files written")


def test_3_create_base_snapshot(sandbox):
    """Create full base snapshot."""
    log("Test 3: Create base snapshot (full)")
    t0 = time.time()

    base = sandbox.create_snapshot(
        snapshot_id=f"snap-base-{int(time.time())}",
        description="Base snapshot with 4 files",
        tags={"chain": "test", "layer": "base"},
    )
    snapshots_to_cleanup.append(base.snapshot_id)

    assert base.snapshot_type == "full"
    assert base.chain_depth == 0
    assert base.parent_snapshot_id is None
    assert base.size_bytes > 0

    # Verify marker was created
    marker_check = sandbox.exec(f"test -f /tmp/.snapshot_marker_{base.snapshot_id} && echo EXISTS")
    assert "EXISTS" in marker_check.stdout, "Marker file not created"

    # Verify manifest was stored
    mgr = SnapshotManager(spaces_config=create_spaces_config_from_env())
    manifest_key = f"snapshots/{base.snapshot_id}/manifest.txt"
    manifest = mgr._spaces.get_object(manifest_key).decode()
    log(f"  Base manifest:\n{manifest}")
    assert "src/main.py" in manifest
    assert "README.md" in manifest

    record("create_base_snapshot", True, time.time() - t0,
           f"id={base.snapshot_id}, size={base.size_bytes}B, type={base.snapshot_type}")
    return base


def test_4_edit_files_and_incremental_1(sandbox, base):
    """Edit 2 files, add 1 new file → incremental snapshot #1."""
    log("Test 4: Edit files → incremental snapshot #1")
    t0 = time.time()

    # Edit main.py
    sandbox.exec("echo 'print(\"hello v2\")' > /home/sandbox/app/src/main.py")
    # Edit config
    sandbox.exec("echo '{\"version\": 2, \"updated\": true}' > /home/sandbox/app/data/config.json")
    # Add new file
    sandbox.exec("echo 'new module' > /home/sandbox/app/src/newmodule.py")

    incr1 = sandbox.create_incremental_snapshot(
        parent_snapshot_id=base.snapshot_id,
        snapshot_id=f"snap-incr1-{int(time.time())}",
        description="Edited main.py, config.json; added newmodule.py",
        tags={"chain": "test", "layer": "incr1"},
    )
    snapshots_to_cleanup.append(incr1.snapshot_id)

    assert incr1.snapshot_type == "incremental"
    assert incr1.parent_snapshot_id == base.snapshot_id
    assert incr1.chain_depth == 1
    assert incr1.files_changed >= 2, f"Expected >=2 changed, got {incr1.files_changed}"
    assert incr1.files_deleted == 0
    assert incr1.size_bytes < base.size_bytes, "Incremental should be smaller than full"

    log(f"  Incremental 1: {incr1.size_bytes}B (base was {base.size_bytes}B), "
        f"changed={incr1.files_changed}, deleted={incr1.files_deleted}")

    record("incremental_snapshot_1", True, time.time() - t0,
           f"id={incr1.snapshot_id}, size={incr1.size_bytes}B, changed={incr1.files_changed}")
    return incr1


def test_5_delete_and_edit_then_incremental_2(sandbox, incr1):
    """Delete 1 file, edit 1 file → incremental snapshot #2."""
    log("Test 5: Delete + edit → incremental snapshot #2")
    t0 = time.time()

    # Delete utils.py
    sandbox.exec("rm /home/sandbox/app/src/utils.py")
    # Edit README
    sandbox.exec("echo 'README v2 - updated' > /home/sandbox/app/README.md")

    incr2 = sandbox.create_incremental_snapshot(
        parent_snapshot_id=incr1.snapshot_id,
        snapshot_id=f"snap-incr2-{int(time.time())}",
        description="Deleted utils.py, edited README.md",
        tags={"chain": "test", "layer": "incr2"},
    )
    snapshots_to_cleanup.append(incr2.snapshot_id)

    assert incr2.snapshot_type == "incremental"
    assert incr2.parent_snapshot_id == incr1.snapshot_id
    assert incr2.chain_depth == 2
    assert incr2.files_deleted >= 1, f"Expected >=1 deleted, got {incr2.files_deleted}"

    log(f"  Incremental 2: {incr2.size_bytes}B, "
        f"changed={incr2.files_changed}, deleted={incr2.files_deleted}")

    record("incremental_snapshot_2", True, time.time() - t0,
           f"id={incr2.snapshot_id}, changed={incr2.files_changed}, deleted={incr2.files_deleted}")
    return incr2


def test_6_resolve_chain(base, incr1, incr2):
    """Verify resolve_chain returns correct ordered chain."""
    log("Test 6: Resolve chain")
    t0 = time.time()

    mgr = SnapshotManager(spaces_config=create_spaces_config_from_env())
    chain = mgr.resolve_chain(incr2.snapshot_id)

    assert len(chain) == 3, f"Expected 3 links, got {len(chain)}"
    assert chain[0].snapshot_id == base.snapshot_id, "First should be base (full)"
    assert chain[0].snapshot_type == "full"
    assert chain[1].snapshot_id == incr1.snapshot_id
    assert chain[1].snapshot_type == "incremental"
    assert chain[2].snapshot_id == incr2.snapshot_id
    assert chain[2].snapshot_type == "incremental"

    chain_str = " → ".join(f"{m.snapshot_id}({m.snapshot_type})" for m in chain)
    log(f"  Chain: {chain_str}")

    record("resolve_chain", True, time.time() - t0, f"chain length={len(chain)}")


def test_7_restore_chain_to_new_sandbox(incr2):
    """Create new sandbox, restore from chain tip, verify ALL state."""
    log("Test 7: Restore chain to new sandbox")
    t0 = time.time()

    sandbox2 = Sandbox.create(
        image="python",
        spaces_config=create_spaces_config_from_env(),
        wait_ready=True,
        timeout=300,
    )
    sandboxes_to_cleanup.append(sandbox2)

    # Restore the full chain from incr2 (resolves: base → incr1 → incr2)
    success = sandbox2.restore_snapshot_chain(incr2.snapshot_id)
    assert success is True

    # ── Verify expected state after chain restore ──

    errors = []

    # Files from base that were NOT deleted should exist
    # main.py was edited in incr1 → should have v2 content
    r = sandbox2.exec("cat /home/sandbox/app/src/main.py")
    if 'hello v2' not in r.stdout:
        errors.append(f"main.py: expected 'hello v2', got {r.stdout.strip()!r}")

    # config.json was edited in incr1 → should have v2 content
    r = sandbox2.exec("cat /home/sandbox/app/data/config.json")
    if '"version": 2' not in r.stdout:
        errors.append(f"config.json: expected version 2, got {r.stdout.strip()!r}")

    # newmodule.py was added in incr1 → should exist
    r = sandbox2.exec("cat /home/sandbox/app/src/newmodule.py")
    if 'new module' not in r.stdout:
        errors.append(f"newmodule.py: expected 'new module', got {r.stdout.strip()!r}")

    # README.md was edited in incr2 → should have v2 content
    r = sandbox2.exec("cat /home/sandbox/app/README.md")
    if 'README v2' not in r.stdout:
        errors.append(f"README.md: expected 'README v2', got {r.stdout.strip()!r}")

    # utils.py was DELETED in incr2 → should NOT exist
    r = sandbox2.exec("test -f /home/sandbox/app/src/utils.py && echo EXISTS || echo GONE")
    if 'GONE' not in r.stdout:
        errors.append(f"utils.py: should be deleted but still exists")

    # List final file state
    r = sandbox2.exec("find /home/sandbox/app -type f | sort")
    log(f"  Restored files:\n{r.stdout}")

    if errors:
        for e in errors:
            log(f"  VERIFICATION ERROR: {e}", "ERROR")
        record("restore_chain_verify", False, time.time() - t0, "; ".join(errors))
        return False
    else:
        record("restore_chain_verify", True, time.time() - t0,
               "all edits applied, deletion confirmed, chain fully restored")
        return True


def test_8_resolve_chain_for_full_snapshot(base):
    """Verify resolve_chain on a full snapshot returns single element."""
    log("Test 8: Resolve chain for full snapshot (single element)")
    t0 = time.time()

    mgr = SnapshotManager(spaces_config=create_spaces_config_from_env())
    chain = mgr.resolve_chain(base.snapshot_id)

    assert len(chain) == 1, f"Expected 1, got {len(chain)}"
    assert chain[0].snapshot_type == "full"

    record("resolve_chain_single", True, time.time() - t0, "single full snapshot in chain")


def test_9_chain_error_handling():
    """Verify broken chain raises SnapshotChainError."""
    log("Test 9: Chain error handling")
    t0 = time.time()

    from do_app_sandbox.exceptions import SnapshotChainError

    mgr = SnapshotManager(spaces_config=create_spaces_config_from_env())

    try:
        mgr.resolve_chain("nonexistent-snapshot-xyz")
        record("chain_error_handling", False, time.time() - t0, "Expected SnapshotChainError")
    except SnapshotChainError as e:
        assert "not found" in str(e).lower() or "broken" in str(e).lower()
        record("chain_error_handling", True, time.time() - t0, f"correctly raised: {e}")


# ── Main ──────────────────────────────────────────────────────────────────

def cleanup():
    log("Cleaning up resources...")
    spaces_config = create_spaces_config_from_env()
    mgr = SnapshotManager(spaces_config=spaces_config)

    for snap_id in snapshots_to_cleanup:
        try:
            # Also clean manifest.txt and deletions.txt
            for suffix in ["archive.tar.gz", "metadata.json", "manifest.txt", "deletions.txt"]:
                key = f"snapshots/{snap_id}/{suffix}"
                try:
                    mgr._spaces.delete_object(key)
                except Exception:
                    pass
            log(f"  Deleted snapshot: {snap_id}")
        except Exception as e:
            log(f"  Failed to delete snapshot {snap_id}: {e}", "WARN")

    for sandbox in sandboxes_to_cleanup:
        try:
            sandbox.delete()
            log(f"  Deleted sandbox: {sandbox._app_id}")
        except Exception as e:
            log(f"  Failed to delete sandbox: {e}", "WARN")


def main():
    log("=" * 70)
    log("Incremental Snapshot Chain E2E Validation")
    log("=" * 70)

    check_env()
    t_start = time.time()

    try:
        sandbox = test_1_create_sandbox()
        test_2_write_initial_files(sandbox)
        base = test_3_create_base_snapshot(sandbox)
        incr1 = test_4_edit_files_and_incremental_1(sandbox, base)
        incr2 = test_5_delete_and_edit_then_incremental_2(sandbox, incr1)
        test_6_resolve_chain(base, incr1, incr2)
        test_7_restore_chain_to_new_sandbox(incr2)
        test_8_resolve_chain_for_full_snapshot(base)
        test_9_chain_error_handling()

    except Exception as e:
        log(f"Test failed with exception: {e}", "ERROR")
        traceback.print_exc()
        record("EXCEPTION", False, 0, str(e))

    finally:
        cleanup()

    total_time = time.time() - t_start
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])

    log("")
    log("=" * 70)
    log(f"SUMMARY: {passed} passed, {failed} failed, {round(total_time, 1)}s total")
    log("=" * 70)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        log(f"  [{status}] {r['test']} ({r['duration_s']}s) {r['details']}")

    report_path = os.path.join(os.path.dirname(__file__), "artifacts", "incremental_snapshot_report.json")
    with open(report_path, "w") as f:
        json.dump({"results": results, "total_time_s": round(total_time, 1), "passed": passed, "failed": failed}, f, indent=2)
    log(f"\nReport: {report_path}")
    log(f"Log:    {LOG_FILE}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
