# Snapshots vs Custom Images

How to choose the right strategy for getting your app running fast in a sandbox.

## The Goal

Sub-second to app-ready for warm pools. Optimized cold starts for everything else.

## Two Strategies

| Strategy | Best For | Cold Start | Warm Pool | Complexity |
|----------|----------|-----------|-----------|------------|
| **Custom image** | Known, stable environments | ~30s (image pull) | **<1s** (instant) | Low — standard Docker workflow |
| **Snapshot** | Ephemeral state, checkpoints, dynamic environments | ~35s (create) + ~3s (restore) | ~1s (acquire) + ~3s (restore) | Medium — requires DO Spaces |

## Custom Images (Recommended for Production)

Build a Docker image with your code and dependencies pre-installed. The container starts ready — no restore step, no Spaces, no extra SDK calls.

### When to Use

- Your app + dependencies are known ahead of time
- You want the fastest possible startup (warm pool: <1s to app-ready)
- You're running the same environment repeatedly (CI, agents, demos)
- You want zero runtime dependencies on Spaces

### How It Works

```dockerfile
# my-app/Dockerfile
FROM ghcr.io/bikramkgupta/sandbox-python:latest

# Install your dependencies at build time
COPY requirements.txt /home/sandbox/app/
RUN cd /home/sandbox/app && uv venv .venv && \
    source .venv/bin/activate && uv pip install -r requirements.txt

# Copy your application code
COPY app.py /home/sandbox/app/
```

Build and push:
```bash
docker build -t ghcr.io/your-org/my-sandbox-app:latest my-app/
docker push ghcr.io/your-org/my-sandbox-app:latest
```

Use with the SDK:
```python
# Cold start — container starts with everything ready
sandbox = Sandbox.create(image="my-sandbox-app", registry="your-org")

# Warm pool — sub-second acquisition, app already installed
manager = SandboxManager(
    pools={"my-app": PoolConfig(target_ready=3)},
    sandbox_defaults={"registry": "your-org"},
)
await manager.start()
sandbox = await manager.acquire(image="my-sandbox-app")  # <1s, app is there
```

### Performance

| Phase | Cold Start | Warm Pool |
|-------|-----------|-----------|
| Container ready | ~30s | <1s |
| App startup | 0s (already installed) | 0s |
| **Total to app-ready** | **~30s** | **<1s** |

The warm pool is effectively a fleet of pre-built containers. Acquiring one is instant — your code, dependencies, and configuration are all baked into the image.

## Snapshots (For Dynamic/Ephemeral State)

Snapshots capture the filesystem state of a running sandbox and store it in DO Spaces. They're restored by downloading and extracting a tar archive into a sandbox.

### When to Use

- Checkpointing during long-running agent tasks (save progress, resume later)
- Hibernate/resume to save costs (snapshot, delete, restore to new sandbox later)
- Forking a running sandbox into multiple variants (the "snapshot, fork, preview" pattern)
- Dynamic environments where the state is determined at runtime, not build time

### How It Works

```python
# Create and set up a sandbox
sandbox = Sandbox.create(image="python", spaces_config=spaces_config)
sandbox.exec("pip install flask numpy pandas")
sandbox.filesystem.upload_file("app.py", "/home/sandbox/app/app.py")

# Snapshot the state
meta = sandbox.create_snapshot(description="app with deps installed")
print(f"Snapshot: {meta.snapshot_id}")  # snap-abc123

# Later: restore to a new sandbox
new_sandbox = Sandbox.create(image="python", spaces_config=spaces_config)
new_sandbox.restore_snapshot("snap-abc123")
# new_sandbox now has flask, numpy, pandas, and app.py

# Or: fork into multiple sandboxes
sandboxes = await asyncio.gather(
    AsyncSandbox.create(image="python", spaces_config=...),
    AsyncSandbox.create(image="python", spaces_config=...),
    AsyncSandbox.create(image="python", spaces_config=...),
)
await asyncio.gather(
    sandboxes[0].restore_snapshot("snap-abc123"),
    sandboxes[1].restore_snapshot("snap-abc123"),
    sandboxes[2].restore_snapshot("snap-abc123"),
)
```

### Performance

| Phase | Cold Start | Warm Pool |
|-------|-----------|-----------|
| Container ready | ~35s | <1s |
| Snapshot restore | ~3s | ~3s |
| **Total to app-ready** | **~38s** | **~4s** |

The ~3s snapshot restore overhead comes from the SDK opening a console session to the container and running `curl | tar`. This happens after the container is running.

## Decision Guide

```
Do you know your environment ahead of time?
├── Yes → Custom image (build once, use forever)
│         └── Need sub-second warm pool? → Custom image + SandboxManager
└── No, state is determined at runtime
    ├── Need to checkpoint/resume? → Snapshots
    ├── Need to fork into variants? → Snapshots
    └── Need to hibernate for cost savings? → Snapshots
```

## Combining Both

Custom images and snapshots are not mutually exclusive:

```python
# Start from a custom image with heavy dependencies pre-installed
sandbox = Sandbox.create(image="ml-sandbox", registry="your-org")

# Do runtime work (training, data processing, etc.)
sandbox.exec("python train.py --epochs 100")

# Snapshot the runtime state (model weights, processed data)
meta = sandbox.create_snapshot(description="trained model checkpoint")

# Resume later from the checkpoint
new_sandbox = Sandbox.create(image="ml-sandbox", registry="your-org")
new_sandbox.restore_snapshot(meta.snapshot_id)
# Has both the pre-installed deps (from image) and trained model (from snapshot)
```

This gives you the best of both worlds:
- **Custom image** handles the slow, stable parts (OS packages, ML frameworks, large dependencies)
- **Snapshots** handle the fast-changing, ephemeral parts (model weights, processed data, runtime config)

## Custom Image Requirements

Custom Dockerfiles must:
1. `EXPOSE 8080` — for user applications (service mode)
2. Have an ENTRYPOINT or CMD
3. Base on the provided sandbox images for compatibility:

```dockerfile
# Python base
FROM ghcr.io/bikramkgupta/sandbox-python:latest

# Node base
FROM ghcr.io/bikramkgupta/sandbox-node:latest

# Service mode (includes FastAPI sandbox API server)
FROM ghcr.io/bikramkgupta/sandbox-python-service:latest
```

See the [Custom Image Requirements](../CLAUDE.md#custom-image-requirements) section for details.
