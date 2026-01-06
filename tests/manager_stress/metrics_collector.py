"""
Metrics collection module for SandboxManager stress tests.

Collects time-series metrics from the SandboxManager and task results.
"""

import asyncio
import csv
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import threading


@dataclass
class PoolSnapshot:
    """Snapshot of pool state at a point in time."""
    timestamp: float
    elapsed_seconds: float

    # Pool counts
    python_ready: int = 0
    python_creating: int = 0
    python_in_use: int = 0
    node_ready: int = 0
    node_creating: int = 0
    node_in_use: int = 0

    # Totals
    total_sandboxes: int = 0
    total_ready: int = 0
    total_creating: int = 0
    total_in_use: int = 0

    # Cumulative counters
    total_acquires: int = 0
    pool_hits: int = 0
    cold_starts: int = 0
    scale_up_events: int = 0
    scale_down_events: int = 0

    # Derived metrics
    pool_hit_rate: float = 0.0
    avg_acquire_latency_ms: float = 0.0


@dataclass
class TaskResult:
    """Result of a single task execution."""
    task_id: str
    user_id: str
    user_group: str
    image: str
    program: str
    category: str

    # Timing
    started_at: float
    ended_at: float
    acquire_latency_ms: float
    execution_duration_s: float

    # Status
    success: bool
    from_pool: bool
    error: Optional[str] = None

    # Program output
    program_output: dict = field(default_factory=dict)


@dataclass
class TestSummary:
    """Summary of entire test run."""
    scenario_name: str
    started_at: str
    ended_at: str
    duration_seconds: float

    # Task stats
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    success_rate: float = 0.0

    # Acquisition stats
    total_acquires: int = 0
    pool_hits: int = 0
    cold_starts: int = 0
    pool_hit_rate: float = 0.0

    # Latency stats
    avg_acquire_latency_ms: float = 0.0
    avg_pool_hit_latency_ms: float = 0.0
    avg_cold_start_latency_ms: float = 0.0
    min_acquire_latency_ms: float = 0.0
    max_acquire_latency_ms: float = 0.0
    p50_acquire_latency_ms: float = 0.0
    p95_acquire_latency_ms: float = 0.0
    p99_acquire_latency_ms: float = 0.0

    # Concurrency stats
    max_concurrent_sandboxes: int = 0
    max_concurrent_python: int = 0
    max_concurrent_node: int = 0

    # Scale events
    scale_up_events: int = 0
    scale_down_events: int = 0

    # By category
    tasks_by_category: dict = field(default_factory=dict)
    tasks_by_image: dict = field(default_factory=dict)
    tasks_by_user_group: dict = field(default_factory=dict)


class MetricsCollector:
    """Collects and stores metrics during stress test execution."""

    def __init__(self, scenario_name: str, output_dir: Path):
        self.scenario_name = scenario_name
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.start_time = time.time()
        self.snapshots: list[PoolSnapshot] = []
        self.task_results: list[TaskResult] = []

        # Cumulative counters (thread-safe)
        self._lock = threading.Lock()
        self._total_acquires = 0
        self._pool_hits = 0
        self._cold_starts = 0
        self._scale_up_events = 0
        self._scale_down_events = 0
        self._acquire_latencies: list[float] = []
        self._pool_hit_latencies: list[float] = []
        self._cold_start_latencies: list[float] = []

        # Generate timestamp for file names
        self._timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    def record_acquire(self, latency_ms: float, from_pool: bool):
        """Record a sandbox acquisition."""
        with self._lock:
            self._total_acquires += 1
            self._acquire_latencies.append(latency_ms)
            if from_pool:
                self._pool_hits += 1
                self._pool_hit_latencies.append(latency_ms)
            else:
                self._cold_starts += 1
                self._cold_start_latencies.append(latency_ms)

    def record_scale_up(self):
        """Record a scale-up event."""
        with self._lock:
            self._scale_up_events += 1

    def record_scale_down(self):
        """Record a scale-down event."""
        with self._lock:
            self._scale_down_events += 1

    def record_task_result(self, result: TaskResult):
        """Record a task completion."""
        with self._lock:
            self.task_results.append(result)

    def take_snapshot(self, manager) -> PoolSnapshot:
        """Take a snapshot of current pool state."""
        now = time.time()
        elapsed = now - self.start_time

        # Get pool states from manager
        # This assumes manager has a get_pool_stats() method or similar
        try:
            stats = manager.get_stats() if hasattr(manager, 'get_stats') else {}
        except Exception:
            stats = {}

        with self._lock:
            pool_hit_rate = (
                self._pool_hits / self._total_acquires * 100
                if self._total_acquires > 0 else 0.0
            )
            avg_latency = (
                sum(self._acquire_latencies) / len(self._acquire_latencies)
                if self._acquire_latencies else 0.0
            )

            snapshot = PoolSnapshot(
                timestamp=now,
                elapsed_seconds=elapsed,
                python_ready=stats.get('python_ready', 0),
                python_creating=stats.get('python_creating', 0),
                python_in_use=stats.get('python_in_use', 0),
                node_ready=stats.get('node_ready', 0),
                node_creating=stats.get('node_creating', 0),
                node_in_use=stats.get('node_in_use', 0),
                total_sandboxes=stats.get('total_sandboxes', 0),
                total_ready=stats.get('python_ready', 0) + stats.get('node_ready', 0),
                total_creating=stats.get('python_creating', 0) + stats.get('node_creating', 0),
                total_in_use=stats.get('python_in_use', 0) + stats.get('node_in_use', 0),
                total_acquires=self._total_acquires,
                pool_hits=self._pool_hits,
                cold_starts=self._cold_starts,
                scale_up_events=self._scale_up_events,
                scale_down_events=self._scale_down_events,
                pool_hit_rate=pool_hit_rate,
                avg_acquire_latency_ms=avg_latency,
            )

            self.snapshots.append(snapshot)
            return snapshot

    def calculate_percentile(self, values: list[float], percentile: float) -> float:
        """Calculate percentile from list of values."""
        if not values:
            return 0.0
        sorted_values = sorted(values)
        k = (len(sorted_values) - 1) * (percentile / 100)
        f = int(k)
        c = f + 1 if f + 1 < len(sorted_values) else f
        return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f]) if f != c else sorted_values[f]

    def generate_summary(self) -> TestSummary:
        """Generate test summary from collected data."""
        end_time = time.time()

        with self._lock:
            # Basic stats
            total_tasks = len(self.task_results)
            successful = sum(1 for r in self.task_results if r.success)
            failed = total_tasks - successful

            # Latency stats
            latencies = self._acquire_latencies.copy()
            pool_latencies = self._pool_hit_latencies.copy()
            cold_latencies = self._cold_start_latencies.copy()

            # Concurrency stats from snapshots
            max_total = max((s.total_sandboxes for s in self.snapshots), default=0)
            max_python = max((s.python_ready + s.python_creating + s.python_in_use for s in self.snapshots), default=0)
            max_node = max((s.node_ready + s.node_creating + s.node_in_use for s in self.snapshots), default=0)

            # By category/image/group
            by_category: dict[str, dict] = {}
            by_image: dict[str, dict] = {}
            by_group: dict[str, dict] = {}

            for result in self.task_results:
                # By category
                if result.category not in by_category:
                    by_category[result.category] = {'total': 0, 'success': 0}
                by_category[result.category]['total'] += 1
                if result.success:
                    by_category[result.category]['success'] += 1

                # By image
                if result.image not in by_image:
                    by_image[result.image] = {'total': 0, 'success': 0}
                by_image[result.image]['total'] += 1
                if result.success:
                    by_image[result.image]['success'] += 1

                # By user group
                if result.user_group not in by_group:
                    by_group[result.user_group] = {'total': 0, 'success': 0}
                by_group[result.user_group]['total'] += 1
                if result.success:
                    by_group[result.user_group]['success'] += 1

            summary = TestSummary(
                scenario_name=self.scenario_name,
                started_at=datetime.fromtimestamp(self.start_time).isoformat(),
                ended_at=datetime.fromtimestamp(end_time).isoformat(),
                duration_seconds=end_time - self.start_time,
                total_tasks=total_tasks,
                successful_tasks=successful,
                failed_tasks=failed,
                success_rate=successful / total_tasks * 100 if total_tasks > 0 else 0.0,
                total_acquires=self._total_acquires,
                pool_hits=self._pool_hits,
                cold_starts=self._cold_starts,
                pool_hit_rate=self._pool_hits / self._total_acquires * 100 if self._total_acquires > 0 else 0.0,
                avg_acquire_latency_ms=sum(latencies) / len(latencies) if latencies else 0.0,
                avg_pool_hit_latency_ms=sum(pool_latencies) / len(pool_latencies) if pool_latencies else 0.0,
                avg_cold_start_latency_ms=sum(cold_latencies) / len(cold_latencies) if cold_latencies else 0.0,
                min_acquire_latency_ms=min(latencies) if latencies else 0.0,
                max_acquire_latency_ms=max(latencies) if latencies else 0.0,
                p50_acquire_latency_ms=self.calculate_percentile(latencies, 50),
                p95_acquire_latency_ms=self.calculate_percentile(latencies, 95),
                p99_acquire_latency_ms=self.calculate_percentile(latencies, 99),
                max_concurrent_sandboxes=max_total,
                max_concurrent_python=max_python,
                max_concurrent_node=max_node,
                scale_up_events=self._scale_up_events,
                scale_down_events=self._scale_down_events,
                tasks_by_category=by_category,
                tasks_by_image=by_image,
                tasks_by_user_group=by_group,
            )

            return summary

    def save_metrics_csv(self) -> Path:
        """Save time-series metrics to CSV."""
        filepath = self.output_dir / f"metrics_{self._timestamp}.csv"

        with open(filepath, 'w', newline='') as f:
            if self.snapshots:
                fieldnames = list(asdict(self.snapshots[0]).keys())
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for snapshot in self.snapshots:
                    writer.writerow(asdict(snapshot))

        return filepath

    def save_tasks_json(self) -> Path:
        """Save task results to JSON."""
        filepath = self.output_dir / f"tasks_{self._timestamp}.json"

        with open(filepath, 'w') as f:
            json.dump([asdict(r) for r in self.task_results], f, indent=2)

        return filepath

    def save_summary_json(self) -> Path:
        """Save test summary to JSON."""
        filepath = self.output_dir / f"summary_{self._timestamp}.json"
        summary = self.generate_summary()

        with open(filepath, 'w') as f:
            json.dump(asdict(summary), f, indent=2)

        return filepath

    def save_all(self) -> dict[str, Path]:
        """Save all metrics files."""
        return {
            'metrics_csv': self.save_metrics_csv(),
            'tasks_json': self.save_tasks_json(),
            'summary_json': self.save_summary_json(),
        }
