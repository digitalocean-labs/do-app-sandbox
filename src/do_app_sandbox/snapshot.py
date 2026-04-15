"""Snapshot management for sandbox state persistence.

This module provides functionality to create, restore, list, and delete
snapshots of sandbox filesystem state. Snapshots are stored in DO Spaces
and include dependencies (node_modules, .venv) for rapid restoration.

Typical use cases:
- Hibernate idle sandboxes (snapshot + delete for cost savings)
- Pre-warm sandboxes with common dependencies
- Create checkpoints during long-running agent tasks
"""

import json
import time
import uuid
from dataclasses import asdict
from typing import TYPE_CHECKING

import logging

from .exceptions import (
    SnapshotChainError,
    SnapshotError,
    SnapshotNotFoundError,
    SnapshotRestoreError,
    SnapshotUploadError,
    SpacesNotConfiguredError,
)

logger = logging.getLogger(__name__)
from .spaces import SpacesClient
from .types import SnapshotMetadata, SpacesConfig

if TYPE_CHECKING:
    from .sandbox import Sandbox

# Default snapshot prefix in Spaces
DEFAULT_SNAPSHOT_PREFIX = "snapshots/"

# Default paths to snapshot
DEFAULT_SNAPSHOT_PATHS = ["/workspace"]

# Default exclude patterns - keep dependencies, exclude caches
DEFAULT_EXCLUDE_PATTERNS = [
    # Python bytecode and caches
    "*.pyc",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    # Git internals (keep .git for branch info)
    ".git/objects",
    ".git/lfs",
    # Node.js caches (keep node_modules itself)
    "node_modules/.cache",
    ".npm",
    ".yarn/cache",
    # Temp files and logs
    "*.log",
    "*.tmp",
    ".env",
    ".env.local",
    # Coverage and test artifacts
    "coverage/",
    ".coverage",
    "htmlcov/",
    # Build artifacts that can be regenerated
    "dist/",
    "build/",
    "*.egg-info/",
]


class SnapshotManager:
    """Manages sandbox snapshots in DO Spaces.

    Snapshots are tar.gz archives of sandbox filesystem paths stored in
    DO Spaces. Metadata is stored alongside each archive as JSON.

    Storage layout:
        snapshots/
        ├── snap-abc123def456/
        │   ├── metadata.json
        │   └── archive.tar.gz
        ├── hibernate-app123-1704672000/
        │   ├── metadata.json
        │   └── archive.tar.gz
        └── ...
    """

    def __init__(self, spaces_config: SpacesConfig | None = None, prefix: str = DEFAULT_SNAPSHOT_PREFIX):
        """Initialize the snapshot manager.

        Args:
            spaces_config: Configuration for DO Spaces. If not provided,
                          will attempt to create from environment variables.
            prefix: Prefix for snapshot objects in Spaces bucket.

        Raises:
            SpacesNotConfiguredError: If Spaces is not configured
        """
        if spaces_config is None:
            from .spaces import create_spaces_config_from_env

            spaces_config = create_spaces_config_from_env()
            if spaces_config is None:
                raise SpacesNotConfiguredError(
                    "Spaces configuration required for snapshots. "
                    "Set SPACES_BUCKET, SPACES_REGION, SPACES_ACCESS_KEY, "
                    "and SPACES_SECRET_KEY environment variables."
                )

        self._spaces = SpacesClient(spaces_config)
        self._prefix = prefix.rstrip("/") + "/"

    def create_snapshot(
        self,
        sandbox: "Sandbox",
        snapshot_id: str | None = None,
        paths: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        description: str | None = None,
        tags: dict[str, str] | None = None,
        timeout: int = 600,
    ) -> SnapshotMetadata:
        """Create a snapshot of sandbox filesystem.

        Snapshots include dependencies (node_modules, .venv) by default
        for rapid startup. Only caches and temp files are excluded.

        Args:
            sandbox: Sandbox to snapshot
            snapshot_id: Optional unique ID (auto-generated if not provided)
            paths: Paths to include (default: ["/workspace"])
            exclude_patterns: Patterns to exclude (default: caches only)
            description: Optional human-readable description
            tags: Optional key-value tags for organization
            timeout: Timeout for tar/upload operations in seconds

        Returns:
            SnapshotMetadata with snapshot details

        Raises:
            SnapshotError: If snapshot creation fails
            SnapshotUploadError: If upload to Spaces fails
        """
        # Generate snapshot ID if not provided
        snapshot_id = snapshot_id or f"snap-{uuid.uuid4().hex[:12]}"
        paths = paths or DEFAULT_SNAPSHOT_PATHS
        exclude_patterns = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS

        # Build tar command with exclusions
        excludes = " ".join(f"--exclude='{p}'" for p in exclude_patterns)
        # Convert absolute paths to relative for tar
        paths_str = " ".join(p.lstrip("/") for p in paths)
        archive = f"/tmp/snapshot_{snapshot_id}.tar.gz"

        # Create the archive
        tar_cmd = f"tar {excludes} -czf {archive} -C / {paths_str}"
        result = sandbox.exec(tar_cmd, timeout=timeout)

        if not result.success:
            raise SnapshotError(f"Failed to create archive: {result.stderr}")

        # Get archive size
        size_result = sandbox.exec(f"stat -c %s {archive}")
        if not size_result.success:
            raise SnapshotError(f"Failed to get archive size: {size_result.stderr}")
        size_bytes = int(size_result.stdout.strip())

        # Upload to Spaces using presigned URL
        spaces_key = f"{self._prefix}{snapshot_id}/archive.tar.gz"
        upload_url = self._spaces.generate_presigned_upload_url(spaces_key, expires_in=3600)

        # Use curl to upload from sandbox (sandbox uploads directly to Spaces)
        upload_cmd = f"curl -sSf -X PUT -T {archive} '{upload_url}'"
        upload_result = sandbox.exec(upload_cmd, timeout=timeout)

        if not upload_result.success:
            raise SnapshotUploadError(f"Failed to upload snapshot: {upload_result.stderr}")

        # Generate and store manifest (sorted file list for incremental diffing)
        paths_str_abs = " ".join(paths)
        find_excludes = " ".join(
            f"-not -path '*/{p}/*'" if p.endswith("/") else f"-not -path '*/{p}'" if "/" not in p else f"-not -path '*/{p}'"
            for p in exclude_patterns
            if not p.startswith("*")
        )
        manifest_cmd = f"find {paths_str_abs} -type f {find_excludes} -printf '%P\\n' 2>/dev/null | sort"
        manifest_result = sandbox.exec(manifest_cmd, timeout=timeout)
        manifest_content = manifest_result.stdout if manifest_result.success else ""

        manifest_key = f"{self._prefix}{snapshot_id}/manifest.txt"
        self._spaces.put_object(manifest_key, manifest_content.encode(), content_type="text/plain")

        # Create and save metadata
        metadata = SnapshotMetadata(
            snapshot_id=snapshot_id,
            created_at=time.time(),
            sandbox_image=sandbox._image,
            size_bytes=size_bytes,
            paths=paths,
            description=description,
            tags=tags or {},
            snapshot_type="full",
        )
        self._save_metadata(metadata)

        # Touch marker file for incremental snapshot support
        sandbox.exec(f"touch /tmp/.snapshot_marker_{snapshot_id}")

        # Cleanup archive in sandbox
        sandbox.exec(f"rm -f {archive}")

        return metadata

    def restore_snapshot(
        self, sandbox: "Sandbox", snapshot_id: str, target_path: str = "/", timeout: int = 600
    ) -> bool:
        """Restore a snapshot to sandbox.

        Args:
            sandbox: Sandbox to restore to
            snapshot_id: ID of snapshot to restore
            target_path: Base path for extraction (default: /)
            timeout: Timeout for download/extract operations

        Returns:
            True if restoration succeeded

        Raises:
            SnapshotNotFoundError: If snapshot doesn't exist
            SnapshotRestoreError: If restoration fails
        """
        # Generate presigned download URL directly — skip metadata check
        # since the caller already knows the snapshot_id exists
        spaces_key = f"{self._prefix}{snapshot_id}/archive.tar.gz"
        download_url = self._spaces.generate_presigned_download_url(spaces_key, expires_in=3600)

        # Single piped command: download and extract in one exec call
        # No temp file, no cleanup needed — streams directly into tar
        restore_cmd = f"curl -sSfL '{download_url}' | tar -xzf - -C {target_path}"
        result = sandbox.exec(restore_cmd, timeout=timeout)

        if not result.success:
            raise SnapshotRestoreError(f"Failed to restore snapshot: {result.stderr}")

        return True

    def get_snapshot(self, snapshot_id: str) -> SnapshotMetadata | None:
        """Get snapshot metadata.

        Args:
            snapshot_id: Snapshot identifier

        Returns:
            SnapshotMetadata or None if not found
        """
        key = f"{self._prefix}{snapshot_id}/metadata.json"
        try:
            content = self._spaces.get_object(key)
            data = json.loads(content.decode())
            return SnapshotMetadata(**data)
        except Exception:
            return None

    def list_snapshots(
        self,
        prefix: str | None = None,
        image: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> list[SnapshotMetadata]:
        """List all snapshots.

        Args:
            prefix: Optional prefix filter for snapshot IDs
            image: Optional filter by sandbox image
            tags: Optional filter by tags (all must match)

        Returns:
            List of SnapshotMetadata, sorted by created_at descending
        """
        search_prefix = f"{self._prefix}{prefix}" if prefix else self._prefix
        keys = self._spaces.list_objects(search_prefix)

        snapshots = []
        for key in keys:
            if key.endswith("/metadata.json"):
                # Extract snapshot_id from key
                # snapshots/snap-xxx/metadata.json -> snap-xxx
                parts = key.replace(self._prefix, "").split("/")
                if len(parts) >= 2:
                    snapshot_id = parts[0]
                    meta = self.get_snapshot(snapshot_id)
                    if meta:
                        # Apply filters
                        if image and meta.sandbox_image != image:
                            continue
                        if tags:
                            if not all(meta.tags.get(k) == v for k, v in tags.items()):
                                continue
                        snapshots.append(meta)

        # Sort by created_at descending (newest first)
        return sorted(snapshots, key=lambda x: x.created_at, reverse=True)

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete a snapshot.

        Args:
            snapshot_id: Snapshot identifier

        Returns:
            True if deletion succeeded
        """
        # Delete archive
        archive_key = f"{self._prefix}{snapshot_id}/archive.tar.gz"
        try:
            self._spaces.delete_object(archive_key)
        except Exception:
            pass  # Archive may not exist

        # Delete metadata
        metadata_key = f"{self._prefix}{snapshot_id}/metadata.json"
        try:
            self._spaces.delete_object(metadata_key)
        except Exception:
            pass  # Metadata may not exist

        return True

    def snapshot_exists(self, snapshot_id: str) -> bool:
        """Check if a snapshot exists.

        Args:
            snapshot_id: Snapshot identifier

        Returns:
            True if snapshot exists
        """
        key = f"{self._prefix}{snapshot_id}/metadata.json"
        return self._spaces.object_exists(key)

    def get_snapshot_download_url(self, snapshot_id: str, expires_in: int = 3600) -> str:
        """Get a presigned URL to download a snapshot archive.

        Args:
            snapshot_id: Snapshot identifier
            expires_in: URL expiration in seconds

        Returns:
            Presigned download URL

        Raises:
            SnapshotNotFoundError: If snapshot doesn't exist
        """
        if not self.snapshot_exists(snapshot_id):
            raise SnapshotNotFoundError(f"Snapshot not found: {snapshot_id}")

        key = f"{self._prefix}{snapshot_id}/archive.tar.gz"
        return self._spaces.generate_presigned_download_url(key, expires_in)

    def _save_metadata(self, metadata: SnapshotMetadata) -> None:
        """Save metadata to Spaces.

        Args:
            metadata: SnapshotMetadata to save
        """
        key = f"{self._prefix}{metadata.snapshot_id}/metadata.json"
        content = json.dumps(asdict(metadata), indent=2).encode()
        self._spaces.put_object(key, content, content_type="application/json")

    def copy_snapshot(
        self,
        source_id: str,
        target_id: str | None = None,
        description: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> SnapshotMetadata:
        """Create a copy of an existing snapshot.

        Args:
            source_id: Source snapshot ID
            target_id: Target snapshot ID (auto-generated if not provided)
            description: Optional new description
            tags: Optional new tags (merged with source tags)

        Returns:
            New SnapshotMetadata

        Raises:
            SnapshotNotFoundError: If source snapshot doesn't exist
        """
        source = self.get_snapshot(source_id)
        if source is None:
            raise SnapshotNotFoundError(f"Snapshot not found: {source_id}")

        target_id = target_id or f"snap-{uuid.uuid4().hex[:12]}"

        # Copy archive
        source_key = f"{self._prefix}{source_id}/archive.tar.gz"
        target_key = f"{self._prefix}{target_id}/archive.tar.gz"

        self._spaces.client.copy_object(
            Bucket=self._spaces.bucket,
            CopySource={"Bucket": self._spaces.bucket, "Key": source_key},
            Key=target_key,
        )

        # Create new metadata
        merged_tags = {**source.tags, **(tags or {})}
        metadata = SnapshotMetadata(
            snapshot_id=target_id,
            created_at=time.time(),
            sandbox_image=source.sandbox_image,
            size_bytes=source.size_bytes,
            paths=source.paths,
            description=description or f"Copy of {source_id}",
            tags=merged_tags,
        )
        self._save_metadata(metadata)

        return metadata

    # =========================================================================
    # Incremental Snapshots
    # =========================================================================

    def resolve_chain(self, snapshot_id: str) -> list[SnapshotMetadata]:
        """Walk parent links to build the ordered snapshot chain.

        Returns a list ordered [root_full, incr_1, ..., target].

        Raises:
            SnapshotChainError: If chain is broken, cyclic, or doesn't
                terminate at a full snapshot
        """
        chain: list[SnapshotMetadata] = []
        visited: set[str] = set()
        current_id: str | None = snapshot_id

        while current_id is not None:
            if current_id in visited:
                raise SnapshotChainError(f"Cycle detected at {current_id}")

            meta = self.get_snapshot(current_id)
            if meta is None:
                raise SnapshotChainError(f"Broken chain: {current_id} not found")

            visited.add(current_id)
            chain.append(meta)

            if meta.snapshot_type == "full" or meta.parent_snapshot_id is None:
                break

            current_id = meta.parent_snapshot_id

        # Verify chain terminates at a full snapshot
        if chain and chain[-1].snapshot_type != "full":
            raise SnapshotChainError(
                f"Chain does not terminate at a full snapshot "
                f"(tip: {chain[-1].snapshot_id}, type: {chain[-1].snapshot_type})"
            )

        # Reverse so root is first: [root_full, incr_1, ..., target]
        chain.reverse()
        return chain

    def create_incremental_snapshot(
        self,
        sandbox: "Sandbox",
        parent_snapshot_id: str,
        snapshot_id: str | None = None,
        paths: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        description: str | None = None,
        tags: dict[str, str] | None = None,
        timeout: int = 600,
    ) -> SnapshotMetadata:
        """Create a snapshot containing only files changed since the parent.

        Uses `find -newer` with a marker file for change detection and
        manifest comparison for deletion tracking.

        Falls back to a full snapshot if the marker file is missing
        (e.g., container was recycled).

        Args:
            sandbox: Sandbox to snapshot
            parent_snapshot_id: ID of the parent snapshot
            snapshot_id: Optional unique ID (auto-generated if not provided)
            paths: Paths to include (default: mode-appropriate working directory)
            exclude_patterns: Patterns to exclude (default: caches only)
            description: Optional human-readable description
            tags: Optional key-value tags
            timeout: Timeout for operations in seconds

        Returns:
            SnapshotMetadata with snapshot_type="incremental"

        Raises:
            SnapshotError: If snapshot creation fails
            SnapshotNotFoundError: If parent snapshot doesn't exist
        """
        # Validate parent exists
        parent = self.get_snapshot(parent_snapshot_id)
        if parent is None:
            raise SnapshotNotFoundError(f"Parent snapshot not found: {parent_snapshot_id}")

        # Check marker file
        marker_path = f"/tmp/.snapshot_marker_{parent_snapshot_id}"
        marker_check = sandbox.exec(f"test -f {marker_path} && echo EXISTS || echo MISSING")

        if "MISSING" in marker_check.stdout:
            logger.warning(
                "Marker file missing for %s — falling back to full snapshot "
                "(container may have been recycled)",
                parent_snapshot_id,
            )
            return self.create_snapshot(
                sandbox=sandbox,
                snapshot_id=snapshot_id,
                paths=paths,
                exclude_patterns=exclude_patterns,
                description=description,
                tags=tags,
                timeout=timeout,
            )

        snapshot_id = snapshot_id or f"snap-{uuid.uuid4().hex[:12]}"
        paths = paths or DEFAULT_SNAPSHOT_PATHS
        exclude_patterns = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS

        paths_str_abs = " ".join(paths)

        # Build find exclusions for -newer search
        find_excludes = []
        for p in exclude_patterns:
            if p.endswith("/"):
                find_excludes.append(f"-not -path '*/{p}*'")
            elif "*" in p:
                find_excludes.append(f"-not -name '{p}'")
            else:
                find_excludes.append(f"-not -path '*/{p}/*' -not -name '{p}'")
        find_exclude_str = " ".join(find_excludes)

        # Find changed files since marker
        find_cmd = (
            f"find {paths_str_abs} -newer {marker_path} "
            f"{find_exclude_str} "
            f"-type f > /tmp/changed_files_{snapshot_id}.txt 2>/dev/null; "
            f"wc -l < /tmp/changed_files_{snapshot_id}.txt"
        )
        find_result = sandbox.exec(find_cmd, timeout=timeout)
        files_changed = int(find_result.stdout.strip()) if find_result.success else 0

        # Generate current manifest
        manifest_cmd = f"find {paths_str_abs} {find_exclude_str} -type f -printf '%P\\n' 2>/dev/null | sort"
        manifest_result = sandbox.exec(manifest_cmd, timeout=timeout)
        current_manifest = manifest_result.stdout if manifest_result.success else ""

        # Download parent manifest and compute deletions
        parent_manifest_key = f"{self._prefix}{parent_snapshot_id}/manifest.txt"
        files_deleted = 0
        try:
            parent_manifest_bytes = self._spaces.get_object(parent_manifest_key)
            parent_manifest = parent_manifest_bytes.decode()

            # Compute deletions: files in parent but not in current
            parent_set = set(parent_manifest.strip().splitlines())
            current_set = set(current_manifest.strip().splitlines())
            deleted_files = sorted(parent_set - current_set)
            files_deleted = len(deleted_files)
            deletions_content = "\n".join(deleted_files) + "\n" if deleted_files else ""
        except Exception:
            # No parent manifest available — skip deletion tracking
            deletions_content = ""
            logger.warning("Could not retrieve parent manifest for %s", parent_snapshot_id)

        # Create incremental archive from changed files only
        archive = f"/tmp/snapshot_{snapshot_id}.tar.gz"
        size_bytes = 0

        if files_changed > 0:
            tar_cmd = f"tar -czf {archive} -C / -T /tmp/changed_files_{snapshot_id}.txt"
            tar_result = sandbox.exec(tar_cmd, timeout=timeout)
            if not tar_result.success:
                raise SnapshotError(f"Failed to create incremental archive: {tar_result.stderr}")

            size_result = sandbox.exec(f"stat -c %s {archive}")
            if size_result.success:
                size_bytes = int(size_result.stdout.strip())

            # Upload archive
            archive_key = f"{self._prefix}{snapshot_id}/archive.tar.gz"
            upload_url = self._spaces.generate_presigned_upload_url(archive_key, expires_in=3600)
            upload_result = sandbox.exec(f"curl -sSf -X PUT -T {archive} '{upload_url}'", timeout=timeout)
            if not upload_result.success:
                raise SnapshotUploadError(f"Failed to upload incremental snapshot: {upload_result.stderr}")

        # Store manifest
        manifest_key = f"{self._prefix}{snapshot_id}/manifest.txt"
        self._spaces.put_object(manifest_key, current_manifest.encode(), content_type="text/plain")

        # Store deletions.txt if any
        if files_deleted > 0:
            deletions_key = f"{self._prefix}{snapshot_id}/deletions.txt"
            self._spaces.put_object(deletions_key, deletions_content.encode(), content_type="text/plain")

        # Save metadata
        metadata = SnapshotMetadata(
            snapshot_id=snapshot_id,
            created_at=time.time(),
            sandbox_image=sandbox._image,
            size_bytes=size_bytes,
            paths=paths,
            description=description,
            tags=tags or {},
            snapshot_type="incremental",
            parent_snapshot_id=parent_snapshot_id,
            chain_depth=parent.chain_depth + 1,
            chain_root_id=parent.chain_root_id or parent.snapshot_id,
            files_changed=files_changed,
            files_deleted=files_deleted,
        )
        self._save_metadata(metadata)

        # Touch new marker
        sandbox.exec(f"touch /tmp/.snapshot_marker_{snapshot_id}")

        # Cleanup temp files
        sandbox.exec(f"rm -f {archive} /tmp/changed_files_{snapshot_id}.txt")

        return metadata

    def restore_snapshot_chain(
        self,
        sandbox: "Sandbox",
        snapshot_id: str,
        target_path: str = "/",
        timeout: int = 600,
    ) -> bool:
        """Restore a snapshot by applying its entire chain.

        Resolves the chain from root full snapshot through all incremental
        layers, applying them in order. Handles file deletions at each layer.

        Args:
            sandbox: Sandbox to restore to
            snapshot_id: ID of the snapshot to restore (can be any in chain)
            target_path: Base path for extraction (default: /)
            timeout: Timeout for operations in seconds

        Returns:
            True if restoration succeeded

        Raises:
            SnapshotChainError: If chain resolution fails
            SnapshotRestoreError: If restoration fails
        """
        chain = self.resolve_chain(snapshot_id)

        for meta in chain:
            # Skip layers with no archive (empty incrementals)
            if meta.size_bytes == 0 and meta.snapshot_type == "incremental":
                logger.info("Skipping empty incremental layer: %s", meta.snapshot_id)
            else:
                # Extract archive
                archive_key = f"{self._prefix}{meta.snapshot_id}/archive.tar.gz"
                download_url = self._spaces.generate_presigned_download_url(archive_key, expires_in=3600)
                restore_cmd = f"curl -sSfL '{download_url}' | tar -xzf - -C {target_path}"
                result = sandbox.exec(restore_cmd, timeout=timeout)
                if not result.success:
                    raise SnapshotRestoreError(
                        f"Failed to restore layer {meta.snapshot_id}: {result.stderr}"
                    )

            # Process deletions for incremental layers
            if meta.snapshot_type == "incremental" and meta.files_deleted > 0:
                deletions_key = f"{self._prefix}{meta.snapshot_id}/deletions.txt"
                try:
                    deletions_bytes = self._spaces.get_object(deletions_key)
                    deletions = deletions_bytes.decode().strip()
                    if deletions:
                        # Determine the base path for deletions from snapshot paths
                        base_path = meta.paths[0] if meta.paths else "/workspace"
                        # Batch-delete using xargs
                        delete_cmd = f"echo '{deletions}' | xargs -d '\\n' -I{{}} rm -f '{base_path}/{{}}'"
                        sandbox.exec(delete_cmd, timeout=timeout)
                except Exception as e:
                    logger.warning("Could not process deletions for %s: %s", meta.snapshot_id, e)

        # Touch marker for the most recent snapshot in chain
        tip = chain[-1]
        sandbox.exec(f"touch /tmp/.snapshot_marker_{tip.snapshot_id}")

        return True
