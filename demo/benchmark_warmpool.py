#!/usr/bin/env python3
"""Warm Pool Benchmark: Cold Start vs Warm Pool timing comparison.

Measures both flows with timing breakdown:
- Cold Start: Create sandbox → Restore snapshot → Start app
- Warm Pool: Acquire from pool (instant) → Restore snapshot → Start app

Usage:
    source .env.demo
    python benchmark_warmpool.py
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from do_app_sandbox import AsyncSandbox, PoolConfig, SandboxManager

DEMO_DIR = Path(__file__).resolve().parent
SNAPSHOT_ID = "snap-17230c9e9a94"  # From our earlier deployment

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


async def measure_cold_start():
    """Measure cold start: create + restore + app start."""
    print("\n" + "=" * 60)
    print("COLD START BENCHMARK")
    print("=" * 60)

    # Phase 1: Create sandbox
    t0 = time.time()
    print("  Creating sandbox (cold start)...")
    sb = await AsyncSandbox.create(
        image="python",
        component_type="service",
        spaces_config=SPACES_CONFIG,
    )
    t1 = time.time()
    base_image_ms = (t1 - t0) * 1000
    print(f"  Base image ready:   {base_image_ms/1000:.1f}s")

    # Phase 2: Restore snapshot
    print("  Restoring snapshot...")
    await asyncio.to_thread(sb._sync_sandbox.restore_snapshot, SNAPSHOT_ID)
    t2 = time.time()
    snapshot_ms = (t2 - t1) * 1000
    print(f"  Snapshot restored:  {snapshot_ms/1000:.1f}s")

    # Phase 3: Start Flask app
    print("  Starting Flask app...")
    await sb.exec(
        "cd /home/sandbox/app && source .venv/bin/activate && nohup python app.py > /tmp/flask.log 2>&1 &",
        timeout=15,
    )
    # Wait for app to be ready
    for _ in range(20):
        r = await sb.exec("curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/ 2>/dev/null", timeout=5)
        if "200" in r.stdout:
            break
        await asyncio.sleep(0.5)
    t3 = time.time()
    app_start_ms = (t3 - t2) * 1000
    total_ms = (t3 - t0) * 1000
    print(f"  App startup:        {app_start_ms/1000:.1f}s")
    print(f"  TOTAL:              {total_ms/1000:.1f}s")

    # Clean up cold start sandbox
    await sb.delete()
    print("  (cold start sandbox deleted)")

    return {
        "base_image_ms": round(base_image_ms),
        "snapshot_restore_ms": round(snapshot_ms),
        "app_startup_ms": round(app_start_ms),
        "total_ms": round(total_ms),
    }


async def measure_warm_pool():
    """Measure warm pool: acquire + restore + app start, for 3 variants."""
    print("\n" + "=" * 60)
    print("WARM POOL BENCHMARK")
    print("=" * 60)

    # Start the pool manager
    print("  Starting SandboxManager with target_ready=3...")
    manager = SandboxManager(
        pools={"python": PoolConfig(target_ready=3, max_ready=3)},
        sandbox_defaults={
            "component_type": "service",
            "spaces_config": SPACES_CONFIG,
        },
    )

    t_pool_start = time.time()
    await manager.start()
    print("  Warming up pool (waiting for 3 sandboxes to be ready)...")
    await manager.warm_up(timeout=300)
    t_pool_ready = time.time()
    pool_warmup_ms = (t_pool_ready - t_pool_start) * 1000
    print(f"  Pool warm-up time:  {pool_warmup_ms/1000:.1f}s (one-time cost)")

    metrics = manager.metrics()
    if "python" in metrics:
        m = metrics["python"]
        print(f"  Pool status: {m.ready} ready, {m.creating} creating")

    # Now acquire 3 sandboxes and deploy variants
    results = []
    sandboxes = []
    variant_items = list(VARIANTS.items())

    for i, (name, variant_file) in enumerate(variant_items):
        print(f"\n  --- {name} ---")

        # Phase 1: Acquire from pool
        t0 = time.time()
        print(f"  [{name}] Acquiring from pool...")
        sb = await manager.acquire(image="python")
        t1 = time.time()
        acquire_ms = (t1 - t0) * 1000
        from_pool = getattr(sb, "_from_pool", False)
        print(f"  [{name}] Acquired in {acquire_ms/1000:.2f}s (from_pool={from_pool})")

        # Phase 2: Restore snapshot
        print(f"  [{name}] Restoring snapshot...")
        await asyncio.to_thread(sb.restore_snapshot, SNAPSHOT_ID)
        t2 = time.time()
        snapshot_ms = (t2 - t1) * 1000
        print(f"  [{name}] Snapshot restored: {snapshot_ms/1000:.1f}s")

        # Phase 3: Upload variant and start app
        print(f"  [{name}] Uploading variant & starting app...")
        await asyncio.to_thread(sb.filesystem.upload_file, str(variant_file), "/home/sandbox/app/app.py")
        await asyncio.to_thread(
            sb.exec,
            "cd /home/sandbox/app && source .venv/bin/activate && nohup python app.py > /tmp/flask.log 2>&1 &",
        )
        for _ in range(20):
            r = await asyncio.to_thread(
                sb.exec, "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/ 2>/dev/null", timeout=5
            )
            if "200" in r.stdout:
                break
            await asyncio.sleep(0.5)
        t3 = time.time()
        app_start_ms = (t3 - t2) * 1000
        total_ms = (t3 - t0) * 1000

        url = sb.get_url()
        print(f"  [{name}] App startup:   {app_start_ms/1000:.1f}s")
        print(f"  [{name}] TOTAL:         {total_ms/1000:.1f}s")
        print(f"  [{name}] URL:           {url}")

        results.append({
            "name": name,
            "pool_acquire_ms": round(acquire_ms),
            "snapshot_restore_ms": round(snapshot_ms),
            "app_startup_ms": round(app_start_ms),
            "total_ms": round(total_ms),
            "from_pool": from_pool,
            "url": url,
            "app_id": sb.app_id,
        })
        sandboxes.append((name, url, sb))

    # Shutdown manager (but don't delete acquired sandboxes — we keep them running)
    await manager.shutdown()

    return results, sandboxes, pool_warmup_ms


async def main():
    # Run cold start benchmark
    cold_start = await measure_cold_start()

    # Run warm pool benchmark
    warm_pool_results, sandboxes, pool_warmup_ms = await measure_warm_pool()

    # Compute averages for warm pool
    avg_acquire = sum(r["pool_acquire_ms"] for r in warm_pool_results) / len(warm_pool_results)
    avg_snapshot = sum(r["snapshot_restore_ms"] for r in warm_pool_results) / len(warm_pool_results)
    avg_app = sum(r["app_startup_ms"] for r in warm_pool_results) / len(warm_pool_results)
    avg_total = sum(r["total_ms"] for r in warm_pool_results) / len(warm_pool_results)

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)

    print(f"\nCOLD START BREAKDOWN:")
    print(f"  Base image ready:     {cold_start['base_image_ms']/1000:.1f}s")
    print(f"  Snapshot restore:     {cold_start['snapshot_restore_ms']/1000:.1f}s")
    print(f"  App startup:          {cold_start['app_startup_ms']/1000:.1f}s")
    print(f"  TOTAL:                {cold_start['total_ms']/1000:.1f}s")

    print(f"\nWARM POOL BREAKDOWN (avg of {len(warm_pool_results)} runs):")
    print(f"  Pool acquire:         {avg_acquire/1000:.2f}s")
    print(f"  Snapshot restore:     {avg_snapshot/1000:.1f}s")
    print(f"  App startup:          {avg_app/1000:.1f}s")
    print(f"  TOTAL:                {avg_total/1000:.1f}s")

    speedup = cold_start["total_ms"] / avg_total if avg_total > 0 else 0
    print(f"\n  SPEEDUP: {speedup:.1f}x faster with warm pool")

    print(f"\nLive Preview URLs (warm pool):")
    for name, url, sb in sandboxes:
        print(f"  {name:20s} {url}")

    # Save results
    all_results = {
        "cold_start": cold_start,
        "warm_pool": warm_pool_results,
        "warm_pool_avg": {
            "pool_acquire_ms": round(avg_acquire),
            "snapshot_restore_ms": round(avg_snapshot),
            "app_startup_ms": round(avg_app),
            "total_ms": round(avg_total),
        },
        "pool_warmup_ms": round(pool_warmup_ms),
        "speedup": round(speedup, 1),
        "snapshot_id": SNAPSHOT_ID,
    }
    results_file = DEMO_DIR / ".benchmark_results.json"
    with open(results_file, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {results_file}")


if __name__ == "__main__":
    asyncio.run(main())
