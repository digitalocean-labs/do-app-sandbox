#!/usr/bin/env python3
"""Deploy the demo live to App Platform with snapshot/fork flow.

Uses AsyncSandbox with component_type="service" (default) which:
- Creates a SERVICE component (public HTTP on port 8080)
- Uses the WORKER image (sandbox-python, port 8080 free for our Flask app)
- Executes commands via doctl console
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from do_app_sandbox import AsyncSandbox

DEMO_DIR = Path(__file__).resolve().parent
SPACES_CONFIG = {
    "access_key": os.environ["SPACES_ACCESS_KEY"],
    "secret_key": os.environ["SPACES_SECRET_KEY"],
    "bucket": os.environ["SPACES_BUCKET"],
    "region": os.environ["SPACES_REGION"],
}

VARIANTS = {
    "color_badges": DEMO_DIR / "variants" / "priority_color_badges.py",
    "drag_reorder": DEMO_DIR / "variants" / "priority_drag_reorder.py",
    "smart_suggest": DEMO_DIR / "variants" / "priority_smart_suggest.py",
}


async def deploy_base():
    """Deploy base app and create snapshot."""
    print("\n=== Phase 1: Deploy Base App ===")
    t0 = time.time()

    print("Creating sandbox (component_type=service, port 8080 exposed)...")
    sandbox = await AsyncSandbox.create(
        image="python",
        component_type="service",
        spaces_config=SPACES_CONFIG,
    )
    print(f"  Created in {time.time() - t0:.1f}s — app_id: {sandbox.app_id}")

    print("Uploading app files...")
    await sandbox.filesystem.upload_file(str(DEMO_DIR / "app.py"), "/home/sandbox/app/app.py")
    await sandbox.filesystem.upload_file(
        str(DEMO_DIR / "requirements.txt"), "/home/sandbox/app/requirements.txt"
    )

    print("Installing Flask...")
    result = await sandbox.exec(
        "cd /home/sandbox/app && uv venv .venv && source .venv/bin/activate && uv pip install -r requirements.txt",
        timeout=120,
    )
    print(f"  {'OK' if result.success else 'FAILED: ' + result.stderr}")

    print("Starting Flask app on port 8080...")
    await sandbox.exec(
        "cd /home/sandbox/app && source .venv/bin/activate && nohup python app.py > /tmp/flask.log 2>&1 &",
        timeout=15,
    )
    await asyncio.sleep(5)

    url = await sandbox.get_url()
    print(f"  Base app live at: {url}")

    print("\n=== Phase 2: Snapshot to Spaces ===")
    t1 = time.time()
    meta = await asyncio.to_thread(
        sandbox._sync_sandbox.create_snapshot,
        description="base todo app with Flask + deps",
        paths=["/home/sandbox/app"],
    )
    print(f"  Snapshot created in {time.time() - t1:.1f}s")
    print(f"  Snapshot ID: {meta.snapshot_id}")
    print(f"  Size: {meta.size_bytes / 1024:.0f} KB")

    return sandbox, meta


async def deploy_variant(name, variant_file, snapshot_id):
    """Deploy a single variant from snapshot."""
    t0 = time.time()
    print(f"  [{name}] Creating sandbox...")

    sb = await AsyncSandbox.create(
        image="python",
        component_type="service",
        spaces_config=SPACES_CONFIG,
    )
    print(f"  [{name}] Ready in {time.time() - t0:.1f}s, restoring snapshot...")

    await asyncio.to_thread(sb._sync_sandbox.restore_snapshot, snapshot_id)
    print(f"  [{name}] Restored, uploading variant...")

    await sb.filesystem.upload_file(str(variant_file), "/home/sandbox/app/app.py")

    print(f"  [{name}] Starting app...")
    await sb.exec(
        "cd /home/sandbox/app && source .venv/bin/activate && nohup python app.py > /tmp/flask.log 2>&1 &",
        timeout=15,
    )
    await asyncio.sleep(5)

    url = await sb.get_url()
    print(f"  [{name}] LIVE at {url} ({time.time() - t0:.1f}s)")
    return name, url, sb


async def deploy_variants(snapshot_id):
    """Deploy all 3 variants in parallel."""
    print("\n=== Phase 3: Fork 3 Previews ===")
    t0 = time.time()

    results = await asyncio.gather(
        *[deploy_variant(name, path, snapshot_id) for name, path in VARIANTS.items()]
    )

    print(f"\n  All 3 deployed in {time.time() - t0:.1f}s!")
    return results


async def main():
    # Phase 1+2
    sandbox, meta = await deploy_base()

    # Phase 3
    previews = await deploy_variants(meta.snapshot_id)

    # Summary
    print("\n" + "=" * 60)
    print("DEPLOYMENT COMPLETE")
    print("=" * 60)
    base_url = await sandbox.get_url()
    print(f"\nBase App:       {base_url}")
    print(f"Base App ID:    {sandbox.app_id}")
    print(f"Snapshot ID:    {meta.snapshot_id}")
    print(f"\nLive Previews:")
    for name, url, sb in previews:
        print(f"  {name:20s} {url}")
        print(f"  {'':20s} app_id: {sb.app_id}")
    print(f"\nSpaces Bucket:  {SPACES_CONFIG['bucket']} ({SPACES_CONFIG['region']})")
    print("=" * 60)

    # Save results
    results_file = DEMO_DIR / ".deploy_results.txt"
    with open(results_file, "w") as f:
        f.write(f"base_url={base_url}\n")
        f.write(f"base_app_id={sandbox.app_id}\n")
        f.write(f"snapshot_id={meta.snapshot_id}\n")
        for name, url, sb in previews:
            f.write(f"{name}_url={url}\n")
            f.write(f"{name}_app_id={sb.app_id}\n")
    print(f"\nResults saved to {results_file}")


if __name__ == "__main__":
    asyncio.run(main())
