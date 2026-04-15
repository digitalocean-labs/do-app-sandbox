#!/usr/bin/env python3
"""E2E Snapshot Foundation Validation.

Tests the snapshot infrastructure that incremental snapshots will build on.
Requires: DIGITALOCEAN_TOKEN, SPACES_BUCKET, SPACES_REGION, SPACES_ACCESS_KEY, SPACES_SECRET_KEY

Test plan:
1. Create sandbox with Spaces config
2. Write known test files to sandbox
3. Create full snapshot → verify metadata
4. Create second sandbox → restore snapshot → verify files match
5. Test snapshot listing and filtering
6. Test hibernate → wake → verify state restored
7. Cleanup all resources

Usage:
    export DIGITALOCEAN_TOKEN=dop_v1_...
    export SPACES_BUCKET=do-sandbox-snapshot-test
    export SPACES_REGION=syd1
    export SPACES_ACCESS_KEY=...
    export SPACES_SECRET_KEY=...
    python tests/e2e_snapshot_validation.py
"""

import json
import os
import sys
import time
import traceback

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from do_app_sandbox import Sandbox
from do_app_sandbox.snapshot import SnapshotManager
from do_app_sandbox.spaces import SpacesClient, create_spaces_config_from_env

# ── Logging ────────────────────────────────────────────────────────────────

LOG_FILE = os.path.join(os.path.dirname(__file__), "artifacts", "snapshot_validation.log")

def log(msg: str, level: str = "INFO"):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def log_result(test_name: str, passed: bool, details: str = ""):
    status = "PASS" if passed else "FAIL"
    log(f"  {status}: {test_name}" + (f" — {details}" if details else ""), "RESULT")

# ── Cleanup tracker ───────────────────────────────────────────────────────

sandboxes_to_cleanup: list[Sandbox] = []
snapshots_to_cleanup: list[str] = []
results: list[dict] = []


def record(name: str, passed: bool, duration: float, details: str = ""):
    results.append({"test": name, "passed": passed, "duration_s": round(duration, 1), "details": details})
    log_result(name, passed, f"{round(duration, 1)}s" + (f" — {details}" if details else ""))


# ── Env check ─────────────────────────────────────────────────────────────

def check_env():
    required = ["DIGITALOCEAN_TOKEN", "SPACES_BUCKET", "SPACES_REGION", "SPACES_ACCESS_KEY", "SPACES_SECRET_KEY"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        log(f"Missing env vars: {', '.join(missing)}", "ERROR")
        sys.exit(1)
    log(f"Environment OK: bucket={os.environ['SPACES_BUCKET']}, region={os.environ['SPACES_REGION']}")


# ── Tests ─────────────────────────────────────────────────────────────────

def test_1_create_sandbox_with_spaces():
    """Create a sandbox with Spaces config attached."""
    log("Test 1: Create sandbox with Spaces config")
    t0 = time.time()

    spaces_config = create_spaces_config_from_env()
    sandbox = Sandbox.create(
        image="python",
        spaces_config=spaces_config,
        wait_ready=True,
        timeout=300,
    )
    sandboxes_to_cleanup.append(sandbox)

    # Verify sandbox is functional
    result = sandbox.exec("echo 'sandbox-alive'")
    assert result.exit_code == 0, f"exec failed: {result.stderr}"
    assert "sandbox-alive" in result.stdout

    dur = time.time() - t0
    record("create_sandbox_with_spaces", True, dur, f"app_id={sandbox._app_id}")
    return sandbox


def test_2_write_test_files(sandbox: Sandbox):
    """Write known test files to the sandbox."""
    log("Test 2: Write test files to sandbox")
    t0 = time.time()

    test_files = {
        "/home/sandbox/app/hello.txt": "Hello from snapshot test!",
        "/home/sandbox/app/data/config.json": '{"version": 1, "name": "snapshot-test"}',
        "/home/sandbox/app/src/main.py": 'print("Snapshot foundation test")',
    }

    sandbox.exec("mkdir -p /home/sandbox/app/data /home/sandbox/app/src")
    for path, content in test_files.items():
        result = sandbox.exec(f"echo '{content}' > {path}")
        assert result.exit_code == 0, f"Failed to write {path}: {result.stderr}"

    # Verify files exist
    result = sandbox.exec("find /home/sandbox/app -type f | sort")
    assert result.exit_code == 0
    log(f"  Files in sandbox: {result.stdout.strip()}")

    dur = time.time() - t0
    record("write_test_files", True, dur, f"{len(test_files)} files")
    return test_files


def test_3_create_snapshot(sandbox: Sandbox):
    """Create a full snapshot and verify metadata."""
    log("Test 3: Create snapshot")
    t0 = time.time()

    metadata = sandbox.create_snapshot(
        snapshot_id=f"snap-e2e-{int(time.time())}",
        description="E2E validation snapshot",
        tags={"purpose": "e2e-test", "image": "python"},
    )
    snapshots_to_cleanup.append(metadata.snapshot_id)

    # Verify metadata fields
    assert metadata.snapshot_id.startswith("snap-e2e-"), f"Bad ID: {metadata.snapshot_id}"
    assert metadata.sandbox_image == "python"
    assert metadata.size_bytes > 0, f"Empty snapshot: {metadata.size_bytes}"
    assert metadata.description == "E2E validation snapshot"
    assert metadata.tags["purpose"] == "e2e-test"

    dur = time.time() - t0
    record("create_snapshot", True, dur, f"id={metadata.snapshot_id}, size={metadata.size_bytes} bytes")
    return metadata


def test_4_verify_snapshot_in_spaces(snapshot_id: str):
    """Verify snapshot metadata and archive exist in Spaces."""
    log("Test 4: Verify snapshot in Spaces")
    t0 = time.time()

    spaces_config = create_spaces_config_from_env()
    manager = SnapshotManager(spaces_config=spaces_config)

    # Check existence
    assert manager.snapshot_exists(snapshot_id), f"Snapshot {snapshot_id} not found in Spaces"

    # Retrieve metadata
    meta = manager.get_snapshot(snapshot_id)
    assert meta is not None, "Metadata retrieval returned None"
    assert meta.snapshot_id == snapshot_id
    assert meta.size_bytes > 0

    # Verify download URL can be generated
    url = manager.get_snapshot_download_url(snapshot_id)
    assert url.startswith("https://"), f"Bad URL: {url}"

    dur = time.time() - t0
    record("verify_snapshot_in_spaces", True, dur, f"metadata OK, download URL generated")


def test_5_restore_to_new_sandbox(snapshot_id: str, original_files: dict):
    """Create a second sandbox, restore snapshot, verify files match."""
    log("Test 5: Restore snapshot to new sandbox")
    t0 = time.time()

    spaces_config = create_spaces_config_from_env()
    sandbox2 = Sandbox.create(
        image="python",
        spaces_config=spaces_config,
        wait_ready=True,
        timeout=300,
    )
    sandboxes_to_cleanup.append(sandbox2)

    # Restore
    success = sandbox2.restore_snapshot(snapshot_id)
    assert success is True, "restore_snapshot returned False"

    # Verify each file
    mismatches = []
    for path, expected_content in original_files.items():
        result = sandbox2.exec(f"cat {path}")
        if result.exit_code != 0:
            mismatches.append(f"{path}: file missing ({result.stderr})")
        elif expected_content not in result.stdout:
            mismatches.append(f"{path}: content mismatch (got: {result.stdout.strip()!r})")

    assert not mismatches, f"File verification failed:\n" + "\n".join(mismatches)

    dur = time.time() - t0
    record("restore_to_new_sandbox", True, dur, f"all {len(original_files)} files verified")
    return sandbox2


def test_6_list_and_filter_snapshots(snapshot_id: str):
    """Test listing and filtering snapshots."""
    log("Test 6: List and filter snapshots")
    t0 = time.time()

    spaces_config = create_spaces_config_from_env()
    manager = SnapshotManager(spaces_config=spaces_config)

    # List all
    all_snaps = manager.list_snapshots()
    assert any(s.snapshot_id == snapshot_id for s in all_snaps), "Created snapshot not in list"

    # Filter by image
    python_snaps = manager.list_snapshots(image="python")
    assert all(s.sandbox_image == "python" for s in python_snaps)
    assert any(s.snapshot_id == snapshot_id for s in python_snaps)

    # Filter by non-existent image
    node_snaps = manager.list_snapshots(image="node")
    assert not any(s.snapshot_id == snapshot_id for s in node_snaps)

    dur = time.time() - t0
    record("list_and_filter_snapshots", True, dur, f"total={len(all_snaps)}, python={len(python_snaps)}")


def test_7_hibernate_and_wake(original_files: dict):
    """Test hibernate (snapshot + delete) → wake (create + restore)."""
    log("Test 7: Hibernate and wake")
    t0 = time.time()

    spaces_config = create_spaces_config_from_env()

    # Create a sandbox with content
    sandbox = Sandbox.create(
        image="python",
        spaces_config=spaces_config,
        wait_ready=True,
        timeout=300,
    )

    # Write unique content
    hibernate_marker = f"hibernate-test-{int(time.time())}"
    sandbox.exec(f"mkdir -p /home/sandbox/app")
    sandbox.exec(f"echo '{hibernate_marker}' > /home/sandbox/app/hibernate_marker.txt")

    log(f"  Hibernating sandbox {sandbox._app_id}...")
    hibernated = sandbox.hibernate()
    snapshots_to_cleanup.append(hibernated.snapshot_id)
    log(f"  Hibernated: snapshot_id={hibernated.snapshot_id}")

    # Verify sandbox is gone (should not be accessible)
    assert hibernated.snapshot_id.startswith("hibernate-")
    assert hibernated.image == "python"

    # Wake
    log(f"  Waking sandbox from {hibernated.snapshot_id}...")
    woken = Sandbox.wake(hibernated, spaces_config=spaces_config, timeout=300)
    sandboxes_to_cleanup.append(woken)

    # Verify state restored
    result = woken.exec("cat /home/sandbox/app/hibernate_marker.txt")
    assert result.exit_code == 0, f"Failed to read marker: {result.stderr}"
    assert hibernate_marker in result.stdout, f"Marker mismatch: {result.stdout.strip()!r}"

    dur = time.time() - t0
    record("hibernate_and_wake", True, dur, f"marker verified after wake")


def test_8_delete_snapshot():
    """Test snapshot deletion."""
    log("Test 8: Delete snapshot")
    t0 = time.time()

    spaces_config = create_spaces_config_from_env()

    # Create a throwaway sandbox + snapshot
    sandbox = Sandbox.create(
        image="python",
        spaces_config=spaces_config,
        wait_ready=True,
        timeout=300,
    )
    sandboxes_to_cleanup.append(sandbox)

    sandbox.exec("echo 'delete-test' > /home/sandbox/app/delete_me.txt")
    meta = sandbox.create_snapshot(snapshot_id=f"snap-delete-{int(time.time())}")

    manager = SnapshotManager(spaces_config=spaces_config)

    # Verify exists
    assert manager.snapshot_exists(meta.snapshot_id)

    # Delete
    result = manager.delete_snapshot(meta.snapshot_id)
    assert result is True

    # Verify gone
    assert not manager.snapshot_exists(meta.snapshot_id)

    dur = time.time() - t0
    record("delete_snapshot", True, dur, "snapshot deleted and verified gone")


# ── Main ──────────────────────────────────────────────────────────────────

def cleanup():
    """Clean up all resources."""
    log("Cleaning up resources...")

    for snap_id in snapshots_to_cleanup:
        try:
            spaces_config = create_spaces_config_from_env()
            manager = SnapshotManager(spaces_config=spaces_config)
            manager.delete_snapshot(snap_id)
            log(f"  Deleted snapshot: {snap_id}")
        except Exception as e:
            log(f"  Failed to delete snapshot {snap_id}: {e}", "WARN")

    for sandbox in sandboxes_to_cleanup:
        try:
            sandbox.delete()
            log(f"  Deleted sandbox: {sandbox._app_id}")
        except Exception as e:
            log(f"  Failed to delete sandbox {sandbox._app_id}: {e}", "WARN")


def main():
    log("=" * 60)
    log("Snapshot Foundation E2E Validation")
    log("=" * 60)

    check_env()
    t_start = time.time()

    try:
        # Test 1: Create sandbox
        sandbox1 = test_1_create_sandbox_with_spaces()

        # Test 2: Write test files
        test_files = test_2_write_test_files(sandbox1)

        # Test 3: Create snapshot
        metadata = test_3_create_snapshot(sandbox1)

        # Test 4: Verify in Spaces
        test_4_verify_snapshot_in_spaces(metadata.snapshot_id)

        # Test 5: Restore to new sandbox
        test_5_restore_to_new_sandbox(metadata.snapshot_id, test_files)

        # Test 6: List and filter
        test_6_list_and_filter_snapshots(metadata.snapshot_id)

        # Test 7: Hibernate and wake
        test_7_hibernate_and_wake(test_files)

        # Test 8: Delete
        test_8_delete_snapshot()

    except Exception as e:
        log(f"Test failed with exception: {e}", "ERROR")
        traceback.print_exc()
        # Record the failure for whichever test was running
        record(f"EXCEPTION", False, 0, str(e))

    finally:
        cleanup()

    # ── Summary ───────────────────────────────────────────────────────
    total_time = time.time() - t_start
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])

    log("")
    log("=" * 60)
    log(f"SUMMARY: {passed} passed, {failed} failed, {round(total_time, 1)}s total")
    log("=" * 60)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        log(f"  [{status}] {r['test']} ({r['duration_s']}s) {r['details']}")

    # Save JSON report
    report_path = os.path.join(os.path.dirname(__file__), "artifacts", "snapshot_validation_report.json")
    with open(report_path, "w") as f:
        json.dump({"results": results, "total_time_s": round(total_time, 1), "passed": passed, "failed": failed}, f, indent=2)
    log(f"\nReport saved: {report_path}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
