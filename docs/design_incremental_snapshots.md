# Incremental Snapshot Design Document

## Status: Implemented (Phase 1-2)
## Author: Design Discussion
## Date: 2026-04-14

---

## TL;DR — User-Facing API

```python
sandbox = Sandbox.create(image="python", spaces_config=spaces_config)

# Full base snapshot (same as before)
base = sandbox.create_snapshot(description="Base")

# Edit files, then take incremental (only changed files, ~95% smaller)
sandbox.exec("echo 'v2' > /home/sandbox/app/version.txt")
incr = sandbox.create_incremental_snapshot(parent_snapshot_id=base.snapshot_id)

# Restore full chain to a new sandbox (resolves: base → incr automatically)
new_sandbox = Sandbox.create(image="python", spaces_config=spaces_config)
new_sandbox.restore_snapshot_chain(incr.snapshot_id)
```

**Users call snapshot methods explicitly.** The SDK does not automatically trigger snapshots — that's an orchestration concern. The three methods are:
- `create_snapshot()` — full snapshot (unchanged)
- `create_incremental_snapshot(parent_snapshot_id)` — delta-only snapshot
- `restore_snapshot_chain(snapshot_id)` — resolve and apply full chain

---

## 1. Overview

### Problem Statement

The current snapshot system creates a **full `tar.gz` archive** of the entire workspace on every `create_snapshot()` call. This has three consequences:

1. **Redundant uploads**: A Python project with `.venv` is 200-400MB. A Node project with `node_modules` is similar. Editing 3 files and snapshotting re-uploads the entire 400MB.
2. **No dependency deduplication**: `node_modules` and `.venv` are included in every snapshot. Ten snapshots of the same project with identical dependencies = 10x the storage cost.
3. **Flat snapshot model**: Snapshots have no ancestry. There is no way to restore from a base + deltas, fork from a point in history, or compact a series of checkpoints.

### Proposed Solution

Extend `SnapshotManager` with three capabilities:

1. **Incremental snapshots** that capture only filesystem changes since the last snapshot
2. **Content-addressed dependency layers** that are stored once per unique lockfile hash and shared across snapshots
3. **Snapshot chains** with ancestry tracking, chain resolution, and compaction

### Constraints

- Sandboxes run in App Platform containers — **no overlayfs control, no rsync**
- All operations go through `sandbox.exec()` (shell commands inside the container)
- Available tools: `bash`, `tar`, `find`, `stat`, `curl`, `git`, `jq`, `gzip`, `python3`
- File transfers use presigned DO Spaces URLs (no credentials inside sandbox)

### Not In Scope

- Automatic snapshot triggers (when to snapshot) — this is an orchestration concern, not an SDK concern
- REST API endpoints for resume/fork — this is a service layer concern
- Retention policies, GC, billing — product/business logic
- Multi-tenant cache sharing — no shared tenancy in current architecture

---

## 2. Goals and Non-Goals

### Goals

1. **Incremental snapshots** — capture only files changed since the last snapshot, reducing upload size by ~95% for typical edit-snapshot cycles
2. **Dependency layer separation** — store `node_modules`/`.venv` once per unique lockfile, content-addressed by lockfile hash
3. **Snapshot chains** — track parent-child relationships, restore by applying layers in sequence
4. **Chain compaction** — merge a chain of incrementals into a single full snapshot when chain depth grows
5. **Change detection** — expose primitives that let consumers know whether a snapshot is worth taking
6. **Deletion tracking** — correctly handle files deleted between snapshots so they don't reappear on restore
7. **Backward compatibility** — existing `create_snapshot()` and `restore_snapshot()` unchanged

### Non-Goals

1. **Automatic snapshot triggers** — the SDK provides primitives; consumers (agent frameworks, CI) decide when to call them
2. **rsync/restic-style chunking** — not available in the container; we use `find -newer` + `tar -T` instead
3. **Overlay filesystem** — can't control the container's FS driver; we simulate layer semantics in userspace
4. **Cross-tenant dedup** — no shared tenancy model in the current architecture
5. **Snapshot streaming/replication** — v1 stores in a single Spaces bucket

---

## 3. Design

### 3.1 Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SnapshotManager                              │
│                                                                     │
│  Existing (unchanged):                                              │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  create_snapshot()     → full tar.gz, upload to Spaces        │ │
│  │  restore_snapshot()    → curl | tar streaming restore         │ │
│  │  list/get/delete/copy  → metadata operations                  │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  New:                                                               │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  create_incremental_snapshot()                                │ │
│  │    → find -newer marker → tar only changed files              │ │
│  │    → store manifest.txt + deletions.txt                       │ │
│  │    → link to parent via parent_snapshot_id                    │ │
│  │                                                               │ │
│  │  restore_snapshot_chain()                                     │ │
│  │    → resolve chain → apply base + incrementals in order       │ │
│  │    → process deletions at each layer                          │ │
│  │    → restore dep layer if referenced                          │ │
│  │                                                               │ │
│  │  create_dep_layer() / restore_dep_layer()                     │ │
│  │    → hash lockfile → content-addressed storage                │ │
│  │    → dedup across snapshots                                   │ │
│  │                                                               │ │
│  │  compact_chain()                                              │ │
│  │    → restore full chain → re-tar as single full snapshot      │ │
│  │                                                               │ │
│  │  smart_snapshot()                                             │ │
│  │    → auto-detect: full vs incremental, separate deps          │ │
│  │    → auto-compact at chain depth threshold                    │ │
│  │                                                               │ │
│  │  resolve_chain()                                              │ │
│  │    → walk parent links → return ordered [root, ..., tip]      │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
            ┌──────────────┐   ┌───────────────┐
            │   Snapshots  │   │  Dep Layers   │
            │  (Spaces)    │   │  (Spaces)     │
            │              │   │               │
            │  snapshots/  │   │  deplayers/   │
            │  ├─ snap-a/  │   │  ├─ sha256-x/ │
            │  ├─ snap-b/  │   │  ├─ sha256-y/ │
            │  └─ ...      │   │  └─ ...       │
            └──────────────┘   └───────────────┘
```

### 3.2 Snapshot Lifecycle

```
                          create_snapshot() [existing]
                                  │
                                  ▼
                         ┌────────────────┐
                         │  Full Snapshot  │  (snap-aaa)
                         │  archive.tar.gz│  ← full workspace tar
                         │  manifest.txt  │  ← NEW: file list
                         │  metadata.json │  ← NEW: snapshot_type="full"
                         └───────┬────────┘
                                 │
                    ··· user edits 3 files ···
                                 │
               create_incremental_snapshot(parent="snap-aaa")
                                 │
                                 ▼
                         ┌────────────────┐
                         │  Incremental   │  (snap-bbb)
                         │  archive.tar.gz│  ← only 3 changed files
                         │  manifest.txt  │  ← current file list
                         │  deletions.txt │  ← files removed since parent
                         │  metadata.json │  ← parent_snapshot_id="snap-aaa"
                         └───────┬────────┘
                                 │
                    ··· user deletes a file, edits 2 ···
                                 │
               create_incremental_snapshot(parent="snap-bbb")
                                 │
                                 ▼
                         ┌────────────────┐
                         │  Incremental   │  (snap-ccc)
                         │  archive.tar.gz│  ← 2 changed files
                         │  deletions.txt │  ← 1 deleted file
                         │  metadata.json │  ← parent_snapshot_id="snap-bbb"
                         └────────────────┘

      Restore chain for snap-ccc:
      ┌──────────┐     ┌──────────┐     ┌──────────┐
      │ snap-aaa │ ──► │ snap-bbb │ ──► │ snap-ccc │
      │  (full)  │     │  (incr)  │     │  (incr)  │
      └──────────┘     └──────────┘     └──────────┘
        extract          extract          extract
        full tar         3 files          2 files
                         (overwrite)      (overwrite)
                                          rm 1 file
```

### 3.3 Dependency Layer Architecture

```
                    ┌────────────────────────────────────┐
                    │           Sandbox                   │
                    │                                    │
                    │  /workspace/                       │
                    │  ├── app.py          ──┐           │
                    │  ├── utils.py          │ Workspace │
                    │  ├── requirements.txt  │ snapshot  │
                    │  ├── .venv/          ──┘           │
                    │  │   └── (200MB)     ── Dep layer  │
                    │  └── data/                         │
                    └────────────────────────────────────┘

    Snapshot without dep separation:         With dep separation:
    ┌──────────────────────────┐            ┌──────────────────────────┐
    │ snap-xxx/archive.tar.gz  │            │ snap-xxx/archive.tar.gz  │
    │         202 MB           │            │         2 MB             │
    │ (app + deps together)    │            │ (app code only)          │
    └──────────────────────────┘            └──────────────────────────┘
                                            ┌──────────────────────────┐
                                            │ deplayer-sha256-f7a2/    │
                                            │   layer.tar.gz  200 MB   │
                                            │   (shared across snaps)  │
                                            └──────────────────────────┘

    10 snapshots, same deps:                10 snapshots, same deps:
    Total: 10 × 202 MB = 2,020 MB          Total: 10 × 2 MB + 200 MB = 220 MB
                                            Savings: ~89%
```

### 3.4 Change Detection Mechanism

Since overlayfs and rsync are unavailable, change detection uses `find -newer` with a marker file.

**After each snapshot**, the SDK touches a marker file inside the container:

```bash
touch /tmp/.snapshot_marker_{snapshot_id}
```

**Before the next incremental snapshot**, the SDK finds files modified since the marker:

```bash
find /workspace -newer /tmp/.snapshot_marker_{parent_id} \
  -not -path '*/node_modules/*' \
  -not -path '*/.venv/*' \
  -not -path '*/__pycache__/*' \
  -not -name '*.pyc' \
  -type f > /tmp/changed_files.txt 2>/dev/null
```

**Deletion detection** uses manifest comparison:

```bash
# Current state
find /workspace -not -path '*/node_modules/*' -not -path '*/.venv/*' \
  -type f -printf '%P\n' | sort > /tmp/manifest_current.txt

# Compare against parent manifest
curl -sSfL '{parent_manifest_url}' | sort > /tmp/manifest_parent.txt
comm -23 /tmp/manifest_parent.txt /tmp/manifest_current.txt > /tmp/deletions.txt
```

**Incremental tar** uses the file list:

```bash
tar -czf /tmp/snapshot_{id}.tar.gz -C / -T /tmp/changed_files.txt
```

**Fallback**: If the marker file is missing (container was recycled), `create_incremental_snapshot()` automatically falls back to a full snapshot and logs a warning.

---

## 4. Storage Layout

### 4.1 Current Layout (Unchanged)

```
snapshots/
├── snap-abc123def456/
│   ├── metadata.json              # SnapshotMetadata as JSON
│   └── archive.tar.gz             # Full workspace tar
```

### 4.2 Extended Layout

```
snapshots/
├── snap-abc123/                   # Full snapshot
│   ├── metadata.json              # snapshot_type: "full"
│   ├── archive.tar.gz             # Full workspace archive
│   └── manifest.txt               # Sorted file list (relative paths)
│
├── snap-def456/                   # Incremental snapshot
│   ├── metadata.json              # snapshot_type: "incremental"
│   │                              # parent_snapshot_id: "snap-abc123"
│   ├── archive.tar.gz             # Only changed files since parent
│   ├── manifest.txt               # Current full file list
│   └── deletions.txt              # Files removed since parent
│
├── hibernate-app123-1704672000/   # Hibernation snapshot (existing)
│   ├── metadata.json
│   └── archive.tar.gz

deplayers/                         # Content-addressed dependency layers
├── deplayer-sha256-a1b2c3d4/
│   ├── metadata.json              # DepLayerMetadata
│   └── layer.tar.gz               # node_modules or .venv archive
│
├── deplayer-sha256-e5f6a7b8/
│   ├── metadata.json
│   └── layer.tar.gz
```

Key design decisions:
- `manifest.txt` stored with **every** snapshot (full and incremental) so deletion detection works without downloading/extracting the parent archive
- `deletions.txt` only present in incremental snapshots
- Dependency layers stored under separate `deplayers/` prefix, content-addressed by lockfile SHA-256
- Existing full snapshots continue to work — new files are additive

---

## 5. Data Model

### 5.1 SnapshotMetadata (Extended)

**File: `src/do_app_sandbox/types.py`**

New fields added to existing `SnapshotMetadata` dataclass. All have defaults for backward compatibility.

```python
@dataclass
class SnapshotMetadata:
    """Metadata about a saved snapshot."""

    # --- Existing fields (unchanged) ---
    snapshot_id: str
    created_at: float
    sandbox_image: str
    size_bytes: int
    paths: list[str]
    description: str | None = None
    tags: dict[str, str] = field(default_factory=dict)

    # --- New fields for incremental snapshots ---
    snapshot_type: str = "full"              # "full" | "incremental"
    parent_snapshot_id: str | None = None    # ID of parent (None for full snapshots)
    chain_depth: int = 0                     # 0 for full, N for Nth incremental in chain
    chain_root_id: str | None = None         # ID of the chain's root full snapshot
    files_changed: int = 0                   # Count of files in this layer's archive
    files_deleted: int = 0                   # Count of files in deletions.txt
    dep_layer_id: str | None = None          # Associated content-addressed dep layer
    lockfile_hash: str | None = None         # SHA-256 of lockfile (for dep layer lookup)
```

**Backward compatibility**: `SnapshotMetadata(**old_json)` works because all new fields have defaults. Existing snapshots implicitly have `snapshot_type="full"`, `parent_snapshot_id=None`, `chain_depth=0`.

### 5.2 DepLayerMetadata (New)

```python
@dataclass
class DepLayerMetadata:
    """Metadata about a content-addressed dependency layer."""

    layer_id: str                            # "deplayer-sha256-{hash[:16]}"
    created_at: float
    lockfile_hash: str                       # Full SHA-256 of lockfile contents
    lockfile_name: str                       # e.g. "package-lock.json", "requirements.txt"
    dep_path: str                            # e.g. "/workspace/node_modules", "/workspace/.venv"
    size_bytes: int
    sandbox_image: str                       # Image this layer was built for
    reference_count: int = 0                 # Snapshots referencing this layer
```

### 5.3 New Exceptions

**File: `src/do_app_sandbox/exceptions.py`**

```python
class SnapshotChainError(SnapshotError):
    """Raised when snapshot chain is broken, cyclic, or invalid."""
    pass

class DepLayerError(SnapshotError):
    """Raised when dependency layer creation or restoration fails."""
    pass
```

---

## 6. API Design

### 6.1 SnapshotManager Methods (New)

All methods are added to `SnapshotManager` in `src/do_app_sandbox/snapshot.py`. Existing methods are unchanged.

#### `create_incremental_snapshot()`

Creates a snapshot containing only files changed since the parent snapshot.

```python
def create_incremental_snapshot(
    self,
    sandbox: "Sandbox",
    parent_snapshot_id: str,
    snapshot_id: str | None = None,
    paths: list[str] | None = None,
    exclude_deps: bool = True,
    description: str | None = None,
    tags: dict[str, str] | None = None,
    timeout: int = 600,
) -> SnapshotMetadata:
```

**Behavior**:
1. Validate parent exists via `get_snapshot(parent_snapshot_id)`
2. Check marker file `/tmp/.snapshot_marker_{parent_snapshot_id}`
   - Missing → fall back to `create_snapshot()` (full), log warning
3. Run `find -newer` to build changed files list (excluding deps if `exclude_deps=True`)
4. Download parent's `manifest.txt`, generate current `manifest.txt`, compute `deletions.txt`
5. If no changes and no deletions → return metadata with `size_bytes=0` (no archive uploaded)
6. Create tar from changed file list only, upload archive + manifest + deletions
7. Touch new marker file

**Returns**: `SnapshotMetadata` with `snapshot_type="incremental"`, `parent_snapshot_id` set, `chain_depth = parent.chain_depth + 1`

#### `restore_snapshot_chain()`

Restores a snapshot by applying its entire chain (root full + all incrementals).

```python
def restore_snapshot_chain(
    self,
    sandbox: "Sandbox",
    snapshot_id: str,
    target_path: str = "/",
    timeout: int = 600,
) -> bool:
```

**Behavior**:
1. Call `resolve_chain(snapshot_id)` to get ordered list `[root_full, incr_1, ..., target]`
2. For each snapshot in chain:
   a. If `size_bytes > 0`: stream-extract archive via `curl | tar`
   b. If incremental and `files_deleted > 0`: download `deletions.txt`, batch-delete via `xargs rm -f`
3. If final snapshot has `dep_layer_id`: call `restore_dep_layer()`
4. Touch marker file for the most recent snapshot

#### `resolve_chain()`

Walks parent links to build the ordered snapshot chain.

```python
def resolve_chain(self, snapshot_id: str) -> list[SnapshotMetadata]:
```

**Behavior**:
1. Start at `snapshot_id`, follow `parent_snapshot_id` links until `snapshot_type == "full"` or `parent_snapshot_id is None`
2. Detect cycles via visited set
3. Return list ordered `[root_full, incr_1, ..., target]`

**Raises**: `SnapshotChainError` if chain is broken (missing parent), cyclic, or doesn't terminate at a full snapshot

#### `create_dep_layer()`

Creates a content-addressed dependency layer from a lockfile and its dep directory.

```python
def create_dep_layer(
    self,
    sandbox: "Sandbox",
    lockfile_path: str,
    dep_path: str,
    timeout: int = 600,
) -> DepLayerMetadata:
```

**Behavior**:
1. Read lockfile via `sandbox.exec(f"cat {lockfile_path}")`
2. Compute SHA-256 on SDK host (Python `hashlib`)
3. Check if `deplayers/deplayer-sha256-{hash[:16]}/metadata.json` exists in Spaces
   - If yes → return existing `DepLayerMetadata` (dedup hit, no upload)
4. Create tar of dep directory: `tar -czf /tmp/deplayer.tar.gz -C / {dep_path}`
5. Upload to `deplayers/deplayer-sha256-{hash[:16]}/layer.tar.gz`
6. Save metadata

#### `restore_dep_layer()`

Restores a dependency layer to the sandbox.

```python
def restore_dep_layer(
    self,
    sandbox: "Sandbox",
    layer_id: str,
    target_path: str = "/",
    timeout: int = 600,
) -> bool:
```

**Behavior**: Generate presigned URL, `curl | tar` extraction. Same pattern as `restore_snapshot()`.

#### `compact_chain()`

Merges a snapshot chain into a single full snapshot.

```python
def compact_chain(
    self,
    sandbox: "Sandbox",
    snapshot_id: str,
    new_snapshot_id: str | None = None,
    delete_old: bool = False,
    timeout: int = 600,
) -> SnapshotMetadata:
```

**Behavior**:
1. `restore_snapshot_chain()` to apply full chain to the sandbox
2. `create_snapshot()` to tar the resulting state as a new full snapshot
3. If `delete_old=True`: delete old chain members (but never delete dep layers — they may be shared)
4. Return new `SnapshotMetadata` with `chain_depth=0`, `parent_snapshot_id=None`

**Note**: Compaction requires a live sandbox because we can't merge tar layers without extracting them first (no rsync/overlayfs).

#### `smart_snapshot()`

High-level convenience method that auto-selects the optimal snapshot strategy.

```python
def smart_snapshot(
    self,
    sandbox: "Sandbox",
    snapshot_id: str | None = None,
    paths: list[str] | None = None,
    description: str | None = None,
    tags: dict[str, str] | None = None,
    max_chain_depth: int = 10,
    auto_compact: bool = True,
    separate_deps: bool = True,
    timeout: int = 600,
) -> SnapshotMetadata:
```

**Behavior**:
1. Look for existing marker files to detect a valid parent
   - No marker → create full snapshot
   - Marker found → create incremental snapshot
2. If `separate_deps=True`: scan for lockfiles, create/reuse dep layers
3. If resulting `chain_depth >= max_chain_depth` and `auto_compact=True`: trigger compaction
4. Return the snapshot metadata

### 6.2 Sandbox Wrapper Methods (New)

**File: `src/do_app_sandbox/sandbox.py`**

Thin wrappers added alongside existing `create_snapshot()` and `restore_snapshot()`:

```python
def create_incremental_snapshot(
    self,
    parent_snapshot_id: str,
    **kwargs,
) -> SnapshotMetadata:
    """Create an incremental snapshot relative to a parent.

    Only captures files changed since the parent snapshot.
    Falls back to full snapshot if the container was recycled.
    """
    self._ensure_awake()
    snapshot_mgr = SnapshotManager(self._spaces_config)
    return snapshot_mgr.create_incremental_snapshot(
        sandbox=self, parent_snapshot_id=parent_snapshot_id, **kwargs
    )

def restore_snapshot_chain(
    self,
    snapshot_id: str,
    target_path: str = "/",
    timeout: int = 600,
) -> bool:
    """Restore a snapshot by applying its full chain.

    Resolves the chain from root full snapshot through all
    incremental layers, applying them in order.
    """
    self._ensure_awake()
    snapshot_mgr = SnapshotManager(self._spaces_config)
    return snapshot_mgr.restore_snapshot_chain(
        sandbox=self, snapshot_id=snapshot_id,
        target_path=target_path, timeout=timeout,
    )

def smart_snapshot(self, **kwargs) -> SnapshotMetadata:
    """Create the optimal snapshot automatically.

    Auto-detects whether to create a full or incremental snapshot,
    optionally separates dependency layers, and auto-compacts
    when chains get too long.
    """
    self._ensure_awake()
    snapshot_mgr = SnapshotManager(self._spaces_config)
    return snapshot_mgr.smart_snapshot(sandbox=self, **kwargs)
```

### 6.3 Lockfile Detection

Known lockfiles and their associated dependency paths:

| Lockfile | Dep Path | Image |
|----------|----------|-------|
| `package-lock.json` | `node_modules` | node |
| `yarn.lock` | `node_modules` | node |
| `pnpm-lock.yaml` | `node_modules` | node |
| `bun.lockb` | `node_modules` | node |
| `requirements.txt` | `.venv` | python |
| `Pipfile.lock` | `.venv` | python |
| `poetry.lock` | `.venv` | python |
| `uv.lock` | `.venv` | python |

Detection scans the workspace root for these files. If multiple exist, the first match (in the order above) is used. The mapping is defined as a constant, not hardcoded in logic.

---

## 7. Algorithms

### 7.1 Chain Resolution

```
resolve_chain("snap-ccc"):

  visited = {}
  chain = []

  current = "snap-ccc"
    → meta = get_snapshot("snap-ccc")
    → snapshot_type = "incremental", parent = "snap-bbb"
    → chain = [snap-ccc]
    → visited = {snap-ccc}

  current = "snap-bbb"
    → meta = get_snapshot("snap-bbb")
    → snapshot_type = "incremental", parent = "snap-aaa"
    → chain = [snap-ccc, snap-bbb]
    → visited = {snap-ccc, snap-bbb}

  current = "snap-aaa"
    → meta = get_snapshot("snap-aaa")
    → snapshot_type = "full"
    → chain = [snap-ccc, snap-bbb, snap-aaa]
    → STOP (reached full snapshot)

  reverse → [snap-aaa, snap-bbb, snap-ccc]
```

Error conditions:
- `get_snapshot()` returns None → `SnapshotChainError("Broken chain: snap-xxx not found")`
- `current_id in visited` → `SnapshotChainError("Cycle detected at snap-xxx")`
- Chain ends without reaching a full snapshot → `SnapshotChainError("Chain does not terminate at a full snapshot")`

### 7.2 Incremental Restore

```
restore_snapshot_chain("snap-ccc"):

  chain = resolve_chain("snap-ccc")
  → [snap-aaa (full), snap-bbb (incr), snap-ccc (incr)]

  Step 1: Apply snap-aaa (full)
    curl -sSfL '{url}' | tar -xzf - -C /
    → Extracts complete workspace

  Step 2: Apply snap-bbb (incremental)
    curl -sSfL '{url}' | tar -xzf - -C /
    → Overwrites 3 changed files in-place
    (deletions.txt empty for this layer)

  Step 3: Apply snap-ccc (incremental)
    curl -sSfL '{url}' | tar -xzf - -C /
    → Overwrites 2 changed files in-place
    curl -sSfL '{deletions_url}' | xargs -d '\n' -I{} rm -f '/workspace/{}'
    → Removes 1 deleted file

  Step 4: Restore dep layer (if snap-ccc.dep_layer_id is set)
    curl -sSfL '{dep_url}' | tar -xzf - -C /
    → Extracts node_modules or .venv

  Step 5: Touch marker
    touch /tmp/.snapshot_marker_snap-ccc
```

### 7.3 Content-Addressed Dedup

```
create_dep_layer(lockfile="/workspace/package-lock.json", dep="/workspace/node_modules"):

  Step 1: Read lockfile content
    sandbox.exec("cat /workspace/package-lock.json")
    → lockfile_content = "{ ... }"

  Step 2: Hash
    hashlib.sha256(lockfile_content.encode()).hexdigest()
    → "f7a2b3c4d5e6..."

  Step 3: Check Spaces
    key = "deplayers/deplayer-sha256-f7a2b3c4d5e6/metadata.json"
    exists? → Yes → return existing DepLayerMetadata (no upload!)

  Step 3 (alt): Not in Spaces
    tar -czf /tmp/deplayer.tar.gz -C / workspace/node_modules
    upload to deplayers/deplayer-sha256-f7a2b3c4d5e6/layer.tar.gz
    save metadata
    return new DepLayerMetadata
```

Two snapshots with identical `package-lock.json` → same dep layer ID → zero additional storage.

---

## 8. Size Impact Analysis

### Typical Python Project

| Component | Size |
|-----------|------|
| Application code | ~2 MB |
| `.venv` | ~250 MB |
| Data/output files | ~10 MB |
| **Total workspace** | **~262 MB** |

| Operation | Full Snapshot | Incremental + Dep Layer |
|-----------|--------------|------------------------|
| First snapshot | 262 MB | 2 MB (code) + 250 MB (dep layer) = 252 MB |
| Edit 3 files, snapshot | 262 MB | ~50 KB (3 files only) |
| Edit 5 files, snapshot | 262 MB | ~100 KB |
| 10 snapshots, same deps | **2,620 MB** | 252 MB + 9 × ~75 KB = **~253 MB** |
| **Storage savings** | — | **~90%** |

### Typical Node Project

| Component | Size |
|-----------|------|
| Application code | ~5 MB |
| `node_modules` | ~400 MB |
| Build output | ~20 MB |
| **Total workspace** | **~425 MB** |

| Operation | Full Snapshot | Incremental + Dep Layer |
|-----------|--------------|------------------------|
| First snapshot | 425 MB | 5 MB + 400 MB = 405 MB |
| Edit 3 files, snapshot | 425 MB | ~80 KB |
| 10 snapshots, same deps | **4,250 MB** | 405 MB + 9 × ~80 KB ≈ **~406 MB** |
| **Storage savings** | — | **~90%** |

### Bandwidth Impact

Incremental snapshot creation requires uploading only the changed files. For a typical edit-snapshot cycle (3-5 files changed):

| Metric | Full Snapshot | Incremental |
|--------|--------------|-------------|
| Upload size | 250-425 MB | 50-100 KB |
| Upload time (10 Mbps) | 200-340s | <1s |
| Spaces egress on restore (full chain of 5) | 250-425 MB | 250 MB (base) + ~400 KB (deltas) |

The restore cost is dominated by the base snapshot. Incremental layers add negligible bandwidth.

---

## 9. Edge Cases and Failure Modes

### 9.1 Marker File Lost

**Scenario**: Container restarts between snapshots. `/tmp/.snapshot_marker_{parent_id}` no longer exists.

**Behavior**: `create_incremental_snapshot()` detects the missing marker and falls back to a full snapshot. The returned metadata has `snapshot_type="full"`. A warning is logged.

**Why**: `find -newer` against a nonexistent file would produce unpredictable results. A full snapshot is always safe.

### 9.2 Clock Skew

**Scenario**: Container's system clock drifts. `find -newer` relies on mtime comparisons.

**Mitigation**: After touching the marker, store the epoch timestamp in snapshot metadata (`created_at`). On the next incremental, verify the marker file's mtime matches `created_at` within a tolerance (e.g., 60 seconds). If not, fall back to full.

### 9.3 /tmp Cleanup

**Scenario**: Container runtime cleans `/tmp` periodically (e.g., `tmpwatch`, `tmpreaper`).

**Mitigation**: Use dot-prefixed filenames (`.snapshot_marker_*`) which most cleanup tools skip. The marker file is <1 byte; no disk pressure concern.

### 9.4 Broken Chain

**Scenario**: A snapshot in the middle of a chain is deleted (e.g., user calls `delete_snapshot()` on a parent).

**Behavior**: `resolve_chain()` raises `SnapshotChainError` with a message identifying the missing link. The chain cannot be restored.

**Prevention**: `delete_snapshot()` should check if the snapshot is referenced as a parent by any other snapshot. If so, refuse deletion or require `force=True`.

### 9.5 Large Deletions

**Scenario**: Thousands of files deleted between snapshots. `deletions.txt` is large.

**Mitigation**: Use `xargs -d '\n' rm -f` for batch deletion instead of per-file shell loop. `xargs` handles argument batching automatically.

### 9.6 Direct Dependency Modification

**Scenario**: User patches a file directly inside `node_modules` (e.g., `node_modules/lodash/index.js`).

**Behavior**: If `exclude_deps=True` (default for incremental), the modification is NOT captured. On restore, the unmodified dep layer is applied.

**Documentation**: Make it clear that dep layers are lockfile-keyed. Direct modifications to dep directories should use `exclude_deps=False`.

### 9.7 Empty Incremental

**Scenario**: No files changed and no files deleted since the parent snapshot.

**Behavior**: No archive is created or uploaded. Metadata is stored with `size_bytes=0`, `files_changed=0`, `files_deleted=0`. On restore, this layer is skipped (no `curl | tar` call).

### 9.8 Concurrent Snapshots

**Scenario**: Two `create_incremental_snapshot()` calls with the same parent from different processes.

**Behavior**: Both succeed independently — they produce different snapshot IDs, both with the same parent. This creates a fork in the chain (two children of one parent). `resolve_chain()` handles this correctly because it walks from child to parent, not parent to children.

---

## 10. Modifications to Existing Code

### 10.1 `create_snapshot()` Additions

Two non-breaking additions to the existing `create_snapshot()` method:

1. **Store `manifest.txt`** alongside the archive after upload:
   ```python
   # After archive upload, generate and store manifest
   manifest_cmd = f"find {paths_str_abs} ... -type f -printf '%P\\n' | sort"
   manifest_result = sandbox.exec(manifest_cmd, timeout=timeout)
   manifest_key = f"{self._prefix}{snapshot_id}/manifest.txt"
   self._spaces.put_object(manifest_key, manifest_result.stdout.encode())
   ```

2. **Touch marker file** after successful snapshot:
   ```python
   sandbox.exec(f"touch /tmp/.snapshot_marker_{snapshot_id}")
   ```

These additions don't change the return type, behavior, or API contract. Existing consumers are unaffected.

### 10.2 `__init__.py` Export Additions

Add to imports and `__all__`:
- `DepLayerMetadata` from `types`
- `SnapshotChainError` from `exceptions`
- `DepLayerError` from `exceptions`

---

## 11. Usage Examples

### 11.1 Basic Incremental Workflow

```python
from do_app_sandbox import Sandbox

sandbox = Sandbox.create(image="python", spaces_config=spaces_config)

# Initial setup
sandbox.exec("cd /workspace && uv venv .venv && uv pip install flask numpy")
sandbox.filesystem.upload_file("app.py", "/workspace/app.py")

# First snapshot (full)
base = sandbox.create_snapshot(description="Initial setup with deps")

# ... user makes changes ...
sandbox.exec("echo 'print(1)' >> /workspace/app.py")
sandbox.filesystem.upload_file("utils.py", "/workspace/utils.py")

# Incremental snapshot (only captures app.py change + new utils.py)
incr = sandbox.create_incremental_snapshot(
    parent_snapshot_id=base.snapshot_id,
    description="Added utils module",
)
print(f"Full snapshot: {base.size_bytes / 1024 / 1024:.1f}MB")
print(f"Incremental:  {incr.size_bytes / 1024:.1f}KB")  # ~2KB vs ~250MB

# Restore to a new sandbox
new_sandbox = Sandbox.create(image="python", spaces_config=spaces_config)
new_sandbox.restore_snapshot_chain(incr.snapshot_id)
```

### 11.2 Smart Snapshot (Auto-Detect)

```python
# First call: no marker exists → creates full snapshot + dep layer
meta1 = sandbox.smart_snapshot(
    description="After setup",
    separate_deps=True,
)
# snapshot_type="full", dep_layer_id="deplayer-sha256-f7a2..."

# Second call: marker exists → creates incremental
sandbox.exec("echo 'v2' > /workspace/version.txt")
meta2 = sandbox.smart_snapshot(description="Version bump")
# snapshot_type="incremental", size_bytes=~50B

# After 10 increments: auto-compacts back to full
for i in range(10):
    sandbox.exec(f"echo '{i}' > /workspace/iter_{i}.txt")
    meta = sandbox.smart_snapshot(max_chain_depth=10)
# At chain_depth=10, auto-compaction creates a new full snapshot
```

### 11.3 Dependency Layer Sharing

```python
from do_app_sandbox.snapshot import SnapshotManager

mgr = SnapshotManager(spaces_config)

# Sandbox A: install deps, create dep layer
sandbox_a = Sandbox.create(image="python", spaces_config=spaces_config)
sandbox_a.exec("cd /workspace && echo 'flask==3.0' > requirements.txt")
sandbox_a.exec("cd /workspace && uv venv .venv && uv pip install -r requirements.txt")

dep = mgr.create_dep_layer(sandbox_a, "/workspace/requirements.txt", "/workspace/.venv")
print(f"Dep layer: {dep.layer_id}")  # deplayer-sha256-abc123...

# Sandbox B: same requirements.txt → reuses existing layer (no upload)
sandbox_b = Sandbox.create(image="python", spaces_config=spaces_config)
sandbox_b.exec("cd /workspace && echo 'flask==3.0' > requirements.txt")

dep2 = mgr.create_dep_layer(sandbox_b, "/workspace/requirements.txt", "/workspace/.venv")
assert dep.layer_id == dep2.layer_id  # Same lockfile hash → same layer

# Restore dep layer to a fresh sandbox
sandbox_c = Sandbox.create(image="python", spaces_config=spaces_config)
mgr.restore_dep_layer(sandbox_c, dep.layer_id)
result = sandbox_c.exec("/workspace/.venv/bin/flask --version")
# flask 3.0 — available without reinstalling
```

### 11.4 Chain Compaction

```python
# Build up a chain
base = sandbox.create_snapshot(description="Base")
for i in range(12):
    sandbox.exec(f"echo 'change {i}' > /workspace/file_{i}.txt")
    sandbox.create_incremental_snapshot(parent_snapshot_id=base.snapshot_id if i == 0 else prev.snapshot_id)

# Chain is now 13 snapshots deep — compact it
mgr = SnapshotManager(spaces_config)
compacted = mgr.compact_chain(sandbox, prev.snapshot_id, delete_old=True)
print(f"Compacted: chain_depth={compacted.chain_depth}")  # 0 (single full snapshot)
```

### 11.5 Pool + Incremental Restore

```python
from do_app_sandbox import SandboxManager, PoolConfig

manager = SandboxManager(
    pools={"python": PoolConfig(target_ready=3)},
    sandbox_defaults={"spaces_config": spaces_config},
)
await manager.start()

# Acquire from pool + restore incremental chain
# Pool acquisition: ~0ms (warm) + chain restore: ~3s (base) + ~50ms (deltas)
sandbox = await manager.acquire_with_snapshot("python", incr.snapshot_id)
```

---

## 12. Implementation Phases

### Phase 1: Foundation

**Scope**: Metadata schema, chain resolution, manifest generation, marker files

**Changes**:
- `types.py`: Add new fields to `SnapshotMetadata`, add `DepLayerMetadata`
- `exceptions.py`: Add `SnapshotChainError`, `DepLayerError`
- `snapshot.py`: Add `resolve_chain()`, modify `create_snapshot()` to store `manifest.txt` and touch marker
- `__init__.py`: Export new types and exceptions

**Tests**:
- Unit: metadata backward compat, `resolve_chain()` with mocked data, cycle detection, broken chain
- Integration: verify `manifest.txt` stored alongside existing snapshot, marker file created

**What it enables**: Chain-aware metadata model. No new user-facing snapshot API yet, but the foundation is in place.

### Phase 2: Incremental Snapshots

**Scope**: `create_incremental_snapshot()`, `restore_snapshot_chain()`

**Changes**:
- `snapshot.py`: Add both methods
- `sandbox.py`: Add wrapper methods on `Sandbox`

**Tests**:
- Unit: mock `sandbox.exec()` → verify exact `find -newer`, `tar -T`, `comm -23` commands
- Integration: full → edit → incremental → restore chain → verify file contents
- Size test: incremental archive must be <1% of full archive size for 1-file edit

**What it enables**: Delta-only snapshots. The primary cost and bandwidth optimization.

### Phase 3: Dependency Layers

**Scope**: `create_dep_layer()`, `restore_dep_layer()`, lockfile detection

**Changes**:
- `snapshot.py`: Add both methods plus lockfile detection constant/logic

**Tests**:
- Unit: content-addressing logic, lockfile hash computation
- Integration: create dep layer → change lockfile → verify new layer ID; same lockfile → verify dedup (no upload)

**What it enables**: Shared dependency storage. The second major cost optimization.

### Phase 4: Smart Snapshot + Compaction

**Scope**: `smart_snapshot()`, `compact_chain()`

**Changes**:
- `snapshot.py`: Add both methods
- `sandbox.py`: Add `smart_snapshot()` wrapper

**Tests**:
- Integration: create chain of N → compact → verify single full snapshot with correct contents
- Integration: `smart_snapshot()` auto-selects full vs incremental correctly

**What it enables**: One-call optimal snapshots. Chain housekeeping.

### Phase 5: Manager Integration

**Scope**: Pool-aware incremental restore

**Changes**:
- `manager.py`: Update `acquire_with_snapshot()` to detect chain and use `restore_snapshot_chain()` when metadata indicates incremental; add `acquire_with_chain()` method

**Tests**:
- Integration: pool acquire + chain restore end-to-end
- Benchmark: compare full restore vs chain restore latency

**What it enables**: Warm pool + incremental restore for minimal total startup time.

---

## 13. Testing Strategy

### 13.1 Unit Tests

| Test | Validates |
|------|-----------|
| Metadata backward compat | `SnapshotMetadata(**old_json)` works, defaults applied |
| `resolve_chain()` - happy path | Returns correct ordered chain |
| `resolve_chain()` - broken chain | Raises `SnapshotChainError` |
| `resolve_chain()` - cycle | Raises `SnapshotChainError` |
| `resolve_chain()` - full snapshot | Returns single-element list |
| Incremental tar commands | Verify `find -newer`, `tar -T` command strings |
| Manifest comparison commands | Verify `comm -23` command string |
| Lockfile hash computation | Known input → known SHA-256 |
| Dep layer content addressing | Same hash → same layer ID |
| Dep layer path detection | Lockfile name → correct dep path |

### 13.2 Integration Tests

| Test | Validates |
|------|-----------|
| Full → incremental → restore chain | Files present and correct |
| Incremental with deletions | Deleted files absent after restore |
| Dep layer create + restore | Dependencies available without install |
| Dep layer dedup | Second create returns same ID, no upload |
| Chain compaction | Result is single full snapshot with all changes |
| Smart snapshot auto-detection | No marker → full; marker → incremental |
| Marker fallback | Remove marker → full snapshot, not error |
| Empty incremental | No changes → `size_bytes=0`, restore skips layer |

### 13.3 Benchmarks

| Benchmark | Measures |
|-----------|----------|
| Incremental vs full snapshot size | Bytes uploaded for 1, 5, 10 file edits |
| Chain restore latency vs full restore | Time to restore chain of N vs single full |
| Dep layer dedup hit ratio | Percentage of dep layer creates that skip upload |

---

## 14. Future Considerations

### Not in V1 but worth tracking

1. **`delete_snapshot()` chain safety**: Check if snapshot is a parent before allowing deletion. V1 raises error on broken chain at restore time; future versions could prevent it at delete time.

2. **Automatic chain compaction in background**: SandboxManager could compact chains during idle replenishment loops.

3. **Dep layer garbage collection**: Reference counting in `DepLayerMetadata`. When `reference_count` drops to 0 (no snapshots reference the layer), it's eligible for deletion.

4. **Snapshot trigger hooks**: A callback interface (`on_snapshot`, `should_snapshot`) that consumers register. The SDK calls these at checkpoints; consumers decide whether to proceed. This is how agent frameworks would integrate automatic triggers without coupling agent logic into the SDK.

5. **Change detection helper**: A public `has_workspace_changed(parent_snapshot_id)` method that returns `bool` + changed file count. Lets consumers decide whether a snapshot is worth taking before paying the tar/upload cost.

6. **zstd compression**: Replace `gzip` with `zstd` for faster compression and better ratios. Requires verifying `zstd` availability in containers (or adding it to images).

---

## 15. Related Files

| File | Role |
|------|------|
| `src/do_app_sandbox/snapshot.py` | Primary implementation — all new methods here |
| `src/do_app_sandbox/types.py` | Data model changes |
| `src/do_app_sandbox/exceptions.py` | New exception classes |
| `src/do_app_sandbox/sandbox.py` | Wrapper methods on Sandbox class |
| `src/do_app_sandbox/manager.py` | Pool integration (Phase 5) |
| `src/do_app_sandbox/spaces.py` | Storage client (unchanged) |
| `src/do_app_sandbox/__init__.py` | Export additions |
| `tests/unit/test_snapshot.py` | Unit tests |
| `tests/integration/test_snapshots.py` | Integration tests |
| `docs/snapshots_vs_custom_images.md` | User-facing docs (update after implementation) |
