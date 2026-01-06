"""
Configuration module for SandboxManager stress tests.

Defines test configurations, pool settings, and scenario definitions.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
import os


class LoadPattern(Enum):
    """Load pattern types for user simulation."""
    BURST = "burst"          # Many acquisitions quickly, then quiet
    STEADY = "steady"        # Consistent rate with small variance
    RANDOM = "random"        # Exponential distribution (Poisson-like)
    PEAK_HOURS = "peak_hours"  # Higher rate during "peak" periods


class ImageType(Enum):
    """Sandbox image types."""
    PYTHON = "python"
    NODE = "node"


@dataclass
class PoolConfig:
    """Configuration for a single sandbox pool."""
    image: ImageType
    target_ready: int = 5      # Target number of warm sandboxes
    max_sandboxes: int = 25    # Maximum sandboxes for this pool


@dataclass
class UserGroupConfig:
    """Configuration for a group of simulated users."""
    name: str
    count: int                          # Number of users in group
    image: ImageType                    # Image type to use
    pattern: LoadPattern                # Load pattern
    task_duration_range: tuple[int, int]  # Min/max task duration in seconds
    categories: list[str] = field(default_factory=lambda: ["compute", "io", "mixed"])


@dataclass
class TestConfig:
    """Main test configuration."""
    # Pool settings
    python_pool: PoolConfig = field(default_factory=lambda: PoolConfig(ImageType.PYTHON))
    node_pool: PoolConfig = field(default_factory=lambda: PoolConfig(ImageType.NODE))

    # Global limits
    max_total_sandboxes: int = 50
    max_concurrent_creates: int = 10

    # Timing settings (in seconds)
    idle_timeout: int = 120           # Time before scale-down starts
    scale_down_delay: int = 60        # Delay between destructions
    cooldown_after_acquire: int = 180 # Pause replenishment after acquire
    max_warm_age: int = 1800          # Max time a sandbox stays warm (30 min)

    # Sandbox defaults
    region: str = "syd1"
    instance_size: str = "apps-s-1vcpu-2gb"

    # Metrics collection
    metrics_interval: int = 10  # Collect metrics every N seconds

    # Paths
    programs_dir: Path = field(default_factory=lambda: Path("sandbox-execution-programs"))
    artifacts_dir: Path = field(default_factory=lambda: Path("tests/artifacts/stress"))


@dataclass
class ScenarioConfig:
    """Configuration for a complete test scenario."""
    name: str
    description: str
    duration_seconds: int
    user_groups: list[UserGroupConfig]
    test_config: TestConfig = field(default_factory=TestConfig)
    idle_periods: list[tuple[int, int]] = field(default_factory=list)  # (start_sec, duration_sec)

    @property
    def total_users(self) -> int:
        return sum(g.count for g in self.user_groups)


# Pre-defined scenarios
def get_quick_validation() -> ScenarioConfig:
    """Quick validation scenario (5-10 min)."""
    return ScenarioConfig(
        name="quick_validation",
        description="Quick validation test - 4 users, basic functionality",
        duration_seconds=600,  # 10 minutes
        user_groups=[
            UserGroupConfig(
                name="python_basic",
                count=2,
                image=ImageType.PYTHON,
                pattern=LoadPattern.STEADY,
                task_duration_range=(60, 120),
                categories=["compute", "io"],
            ),
            UserGroupConfig(
                name="node_basic",
                count=2,
                image=ImageType.NODE,
                pattern=LoadPattern.STEADY,
                task_duration_range=(60, 120),
                categories=["compute", "io"],
            ),
        ],
        test_config=TestConfig(
            python_pool=PoolConfig(ImageType.PYTHON, target_ready=2, max_sandboxes=4),
            node_pool=PoolConfig(ImageType.NODE, target_ready=2, max_sandboxes=4),
            max_total_sandboxes=8,
        ),
    )


def get_burst_test() -> ScenarioConfig:
    """Burst test scenario (30 min)."""
    return ScenarioConfig(
        name="burst_test",
        description="Burst test - 20 users, tests pool exhaustion",
        duration_seconds=1800,  # 30 minutes
        user_groups=[
            UserGroupConfig(
                name="python_burst",
                count=10,
                image=ImageType.PYTHON,
                pattern=LoadPattern.BURST,
                task_duration_range=(60, 300),
                categories=["compute", "io", "mixed"],
            ),
            UserGroupConfig(
                name="node_burst",
                count=10,
                image=ImageType.NODE,
                pattern=LoadPattern.BURST,
                task_duration_range=(60, 300),
                categories=["compute", "io", "async"],
            ),
        ],
        test_config=TestConfig(
            python_pool=PoolConfig(ImageType.PYTHON, target_ready=3, max_sandboxes=15),
            node_pool=PoolConfig(ImageType.NODE, target_ready=3, max_sandboxes=15),
            max_total_sandboxes=30,
        ),
    )


def get_steady_state() -> ScenarioConfig:
    """Steady state scenario (1 hour)."""
    return ScenarioConfig(
        name="steady_state",
        description="Steady state test - 24 users, sustained load",
        duration_seconds=3600,  # 1 hour
        user_groups=[
            UserGroupConfig(
                name="python_steady",
                count=12,
                image=ImageType.PYTHON,
                pattern=LoadPattern.STEADY,
                task_duration_range=(120, 600),
                categories=["compute", "io", "mixed", "network"],
            ),
            UserGroupConfig(
                name="node_steady",
                count=12,
                image=ImageType.NODE,
                pattern=LoadPattern.STEADY,
                task_duration_range=(120, 600),
                categories=["compute", "io", "async", "mixed"],
            ),
        ],
        test_config=TestConfig(
            python_pool=PoolConfig(ImageType.PYTHON, target_ready=4, max_sandboxes=20),
            node_pool=PoolConfig(ImageType.NODE, target_ready=4, max_sandboxes=20),
            max_total_sandboxes=40,
        ),
    )


def get_scale_cycle() -> ScenarioConfig:
    """Scale cycle scenario (90 min)."""
    return ScenarioConfig(
        name="scale_cycle",
        description="Scale cycle test - 30 users, peak hours pattern with idle periods",
        duration_seconds=5400,  # 90 minutes
        user_groups=[
            UserGroupConfig(
                name="python_peak",
                count=15,
                image=ImageType.PYTHON,
                pattern=LoadPattern.PEAK_HOURS,
                task_duration_range=(180, 900),
                categories=["compute", "io", "mixed", "idle"],
            ),
            UserGroupConfig(
                name="node_peak",
                count=15,
                image=ImageType.NODE,
                pattern=LoadPattern.PEAK_HOURS,
                task_duration_range=(180, 900),
                categories=["compute", "io", "async", "mixed", "idle"],
            ),
        ],
        test_config=TestConfig(
            python_pool=PoolConfig(ImageType.PYTHON, target_ready=5, max_sandboxes=20),
            node_pool=PoolConfig(ImageType.NODE, target_ready=5, max_sandboxes=20),
            max_total_sandboxes=40,
        ),
        idle_periods=[
            (1800, 600),   # 10 min idle at 30 min mark
            (4200, 600),   # 10 min idle at 70 min mark
        ],
    )


def get_full_stress() -> ScenarioConfig:
    """Full stress scenario (2+ hours)."""
    return ScenarioConfig(
        name="full_stress",
        description="Full stress test - 50 users, mixed patterns, 2+ hours",
        duration_seconds=7800,  # 2 hours 10 minutes
        user_groups=[
            # Heavy Python users
            UserGroupConfig(
                name="python_heavy",
                count=8,
                image=ImageType.PYTHON,
                pattern=LoadPattern.STEADY,
                task_duration_range=(300, 1800),  # 5-30 min
                categories=["compute", "mixed"],
            ),
            # Burst Python users
            UserGroupConfig(
                name="python_burst",
                count=5,
                image=ImageType.PYTHON,
                pattern=LoadPattern.BURST,
                task_duration_range=(60, 600),  # 1-10 min
                categories=["io", "network"],
            ),
            # Light Python users
            UserGroupConfig(
                name="python_light",
                count=7,
                image=ImageType.PYTHON,
                pattern=LoadPattern.RANDOM,
                task_duration_range=(60, 300),  # 1-5 min
                categories=["compute", "io", "idle"],
            ),
            # Long-running Python
            UserGroupConfig(
                name="python_long",
                count=5,
                image=ImageType.PYTHON,
                pattern=LoadPattern.STEADY,
                task_duration_range=(900, 3000),  # 15-50 min
                categories=["mixed", "idle"],
            ),
            # Heavy Node users
            UserGroupConfig(
                name="node_heavy",
                count=8,
                image=ImageType.NODE,
                pattern=LoadPattern.STEADY,
                task_duration_range=(300, 1800),
                categories=["compute", "mixed"],
            ),
            # Burst Node users
            UserGroupConfig(
                name="node_burst",
                count=5,
                image=ImageType.NODE,
                pattern=LoadPattern.BURST,
                task_duration_range=(60, 600),
                categories=["async", "io"],
            ),
            # Light Node users
            UserGroupConfig(
                name="node_light",
                count=7,
                image=ImageType.NODE,
                pattern=LoadPattern.RANDOM,
                task_duration_range=(60, 300),
                categories=["compute", "io", "idle"],
            ),
            # Long-running Node
            UserGroupConfig(
                name="node_long",
                count=5,
                image=ImageType.NODE,
                pattern=LoadPattern.STEADY,
                task_duration_range=(900, 3000),
                categories=["mixed", "idle"],
            ),
        ],
        test_config=TestConfig(
            python_pool=PoolConfig(ImageType.PYTHON, target_ready=5, max_sandboxes=25),
            node_pool=PoolConfig(ImageType.NODE, target_ready=5, max_sandboxes=25),
            max_total_sandboxes=50,
            max_concurrent_creates=10,
        ),
        idle_periods=[
            (1800, 300),   # 5 min idle at 30 min
            (3600, 300),   # 5 min idle at 60 min
            (5400, 300),   # 5 min idle at 90 min
            (7200, 300),   # 5 min idle at 120 min
        ],
    )


SCENARIOS = {
    "quick_validation": get_quick_validation,
    "burst_test": get_burst_test,
    "steady_state": get_steady_state,
    "scale_cycle": get_scale_cycle,
    "full_stress": get_full_stress,
}


def get_scenario(name: str) -> ScenarioConfig:
    """Get a scenario by name."""
    if name not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {name}. Available: {list(SCENARIOS.keys())}")
    return SCENARIOS[name]()


def get_programs_path() -> Path:
    """Get the path to sandbox execution programs."""
    # Try relative to project root
    candidates = [
        Path("sandbox-execution-programs"),
        Path(__file__).parent.parent.parent / "sandbox-execution-programs",
    ]

    for path in candidates:
        if path.exists():
            return path.resolve()

    raise FileNotFoundError("Could not find sandbox-execution-programs directory")


def get_artifacts_path() -> Path:
    """Get the path to test artifacts directory."""
    path = Path(__file__).parent.parent / "artifacts" / "stress"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
