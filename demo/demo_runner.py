#!/usr/bin/env python3
"""Deploy Conference Demo Runner.

Orchestrates the full "Snapshot, Fork, Preview" demo flow:
1. Create a sandbox and deploy the base todo app
2. Snapshot the running app (with deps) to DO Spaces
3. Fork 3 new sandboxes from the snapshot
4. Upload each feature variant and start them
5. Print all 3 public URLs for side-by-side comparison
6. Teardown on command

Usage:
    # Full demo (interactive)
    python demo_runner.py

    # Pre-create snapshot for faster live demo
    python demo_runner.py --pre-snapshot

    # Run demo from existing snapshot
    python demo_runner.py --from-snapshot <snapshot_id>

    # Cleanup all demo sandboxes
    python demo_runner.py --cleanup

Requires:
    - DIGITALOCEAN_TOKEN environment variable
    - SPACES_ACCESS_KEY, SPACES_SECRET_KEY, SPACES_BUCKET, SPACES_REGION env vars
    - pip install do-app-sandbox
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# Ensure the SDK is importable (works from repo root or with pip install)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from do_app_sandbox import AsyncSandbox, Sandbox, SandboxMode

DEMO_DIR = Path(__file__).resolve().parent
APP_FILE = DEMO_DIR / "app.py"
VARIANTS = {
    "color_badges": DEMO_DIR / "variants" / "priority_color_badges.py",
    "drag_reorder": DEMO_DIR / "variants" / "priority_drag_reorder.py",
    "smart_suggest": DEMO_DIR / "variants" / "priority_smart_suggest.py",
}


def get_spaces_config():
    """Build spaces_config from environment variables."""
    required = ["SPACES_ACCESS_KEY", "SPACES_SECRET_KEY", "SPACES_BUCKET", "SPACES_REGION"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        print("Spaces is required for snapshot/restore. Set these env vars and retry.")
        sys.exit(1)
    return {
        "access_key": os.environ["SPACES_ACCESS_KEY"],
        "secret_key": os.environ["SPACES_SECRET_KEY"],
        "bucket": os.environ["SPACES_BUCKET"],
        "region": os.environ["SPACES_REGION"],
    }


def create_base_sandbox(spaces_config):
    """Create a sandbox and deploy the base todo app."""
    print("\n--- Act 1: Zero to Sandbox ---")

    t0 = time.time()
    print("Creating sandbox...")
    sandbox = Sandbox.create(
        image="python",
        mode=SandboxMode.SERVICE,
        spaces_config=spaces_config,
    )
    print(f"  Sandbox created in {time.time() - t0:.1f}s: {sandbox.app_id}")

    # Upload app and requirements
    t1 = time.time()
    print("Uploading app files...")
    sandbox.filesystem.upload_file(str(APP_FILE), "/home/sandbox/app/app.py")
    sandbox.filesystem.upload_file(str(DEMO_DIR / "requirements.txt"), "/home/sandbox/app/requirements.txt")
    print(f"  Files uploaded in {time.time() - t1:.1f}s")

    # Install dependencies
    t2 = time.time()
    print("Installing dependencies...")
    result = sandbox.exec(
        "cd /home/sandbox/app && uv venv .venv && source .venv/bin/activate && uv pip install -r requirements.txt",
        timeout=120,
    )
    if not result.success:
        print(f"  ERROR: {result.stderr}")
        sys.exit(1)
    print(f"  Dependencies installed in {time.time() - t2:.1f}s")

    # Start the app
    print("Starting todo app...")
    sandbox.launch_process(
        "cd /home/sandbox/app && source .venv/bin/activate && python app.py",
        cwd="/home/sandbox/app",
    )
    time.sleep(3)  # Give Flask a moment to start

    url = sandbox.get_url()
    print(f"\n  Base app live at: {url}")
    print(f"  Total setup time: {time.time() - t0:.1f}s")

    return sandbox


def snapshot_sandbox(sandbox):
    """Snapshot the running sandbox to Spaces."""
    print("\n--- Snapshot ---")
    t0 = time.time()
    print("Creating snapshot...")
    meta = sandbox.create_snapshot(
        description="base todo app with Flask + deps installed",
        paths=["/home/sandbox/app"],
    )
    print(f"  Snapshot created in {time.time() - t0:.1f}s")
    print(f"  Snapshot ID: {meta.snapshot_id}")
    print(f"  Size: {meta.size_bytes / 1024:.0f} KB")
    return meta


async def deploy_preview(name, variant_file, snapshot_id, spaces_config):
    """Deploy a single feature variant from a snapshot."""
    t0 = time.time()
    print(f"  [{name}] Creating sandbox...")

    sb = await AsyncSandbox.create(
        image="python",
        component_type="service",
        spaces_config=spaces_config,
    )
    print(f"  [{name}] Sandbox ready in {time.time() - t0:.1f}s, restoring snapshot...")

    await asyncio.to_thread(sb.restore_snapshot, snapshot_id)
    print(f"  [{name}] Snapshot restored, uploading variant...")

    await sb.filesystem.upload_file(str(variant_file), "/home/sandbox/app/app.py")

    # Start the variant app
    await sb.exec(
        "cd /home/sandbox/app && source .venv/bin/activate && python app.py &",
        timeout=10,
    )
    await asyncio.sleep(3)

    url = sb.get_url()
    elapsed = time.time() - t0
    print(f"  [{name}] Live at {url} ({elapsed:.1f}s)")
    return name, url, sb


async def fork_previews(snapshot_id, spaces_config):
    """Fork 3 previews from the snapshot in parallel."""
    print("\n--- Act 3: Snapshot, Fork, Preview ---")
    t0 = time.time()

    results = await asyncio.gather(
        *[deploy_preview(name, path, snapshot_id, spaces_config) for name, path in VARIANTS.items()]
    )

    print(f"\n  All 3 previews deployed in {time.time() - t0:.1f}s!")
    print("\n  Preview URLs:")
    for name, url, _ in results:
        print(f"    {name:20s} {url}")

    return results


def cleanup_sandboxes(sandboxes, keep=None):
    """Delete sandboxes, optionally keeping one."""
    print("\n--- Teardown ---")
    for name, _, sb in sandboxes:
        if name == keep:
            print(f"  Keeping {name}")
            continue
        print(f"  Deleting {name}...")
        sb.delete()
    print("  Done.")


def run_full_demo(args):
    """Run the complete demo flow."""
    spaces_config = get_spaces_config()

    if args.from_snapshot:
        # Skip base app creation, go straight to forking
        snapshot_id = args.from_snapshot
        print(f"Using existing snapshot: {snapshot_id}")
    else:
        # Act 1: Create and deploy base app
        sandbox = create_base_sandbox(spaces_config)

        input("\nPress Enter to snapshot and continue...")

        # Snapshot
        meta = snapshot_sandbox(sandbox)
        snapshot_id = meta.snapshot_id

        # Delete the base sandbox (we'll fork from snapshot)
        print("Deleting base sandbox...")
        sandbox.delete()

    # Act 3: Fork 3 previews
    results = asyncio.run(fork_previews(snapshot_id, spaces_config))

    # Interactive teardown
    print("\nWhich preview should we keep?")
    for i, (name, _, _) in enumerate(results):
        print(f"  {i + 1}. {name}")
    print(f"  {len(results) + 1}. Delete all")

    try:
        choice = input("\nChoice: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            keep_name = results[int(choice) - 1][0]
            cleanup_sandboxes(results, keep=keep_name)
        else:
            cleanup_sandboxes(results)
    except (KeyboardInterrupt, EOFError):
        print("\nSkipping teardown. Clean up later with --cleanup")


def run_pre_snapshot(args):
    """Create base sandbox and snapshot, save snapshot ID for later."""
    spaces_config = get_spaces_config()

    sandbox = create_base_sandbox(spaces_config)
    meta = snapshot_sandbox(sandbox)

    # Save snapshot ID for the live demo
    snapshot_file = DEMO_DIR / ".snapshot_id"
    snapshot_file.write_text(meta.snapshot_id)

    print(f"\nSnapshot ID saved to {snapshot_file}")
    print(f"Run the demo with: python demo_runner.py --from-snapshot {meta.snapshot_id}")

    # Clean up the base sandbox
    print("Deleting base sandbox...")
    sandbox.delete()
    print("Done. Ready for live demo.")


def run_cleanup(args):
    """Delete all demo sandboxes."""
    import subprocess

    print("Finding demo sandboxes...")
    result = subprocess.run(
        ["doctl", "apps", "list", "--format", "ID,Spec.Name", "--no-header"],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("sandbox-"):
            app_id = parts[0]
            name = parts[1]
            print(f"  Deleting {name} ({app_id})...")
            subprocess.run(["doctl", "apps", "delete", app_id, "--force"], capture_output=True)
    print("Cleanup complete.")


def main():
    parser = argparse.ArgumentParser(description="Deploy Conference Demo Runner")
    parser.add_argument("--pre-snapshot", action="store_true", help="Pre-create snapshot for faster live demo")
    parser.add_argument("--from-snapshot", type=str, help="Use existing snapshot ID")
    parser.add_argument("--cleanup", action="store_true", help="Delete all demo sandboxes")
    args = parser.parse_args()

    if args.cleanup:
        run_cleanup(args)
    elif args.pre_snapshot:
        run_pre_snapshot(args)
    else:
        run_full_demo(args)


if __name__ == "__main__":
    main()
