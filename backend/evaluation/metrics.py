"""Pure metric functions for the synthetic classification evaluation."""

from dataclasses import dataclass
from math import sqrt

RELEVANT = ("applied", "review_required")


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    expected: str
    actual: str
    # For an actual automatic update: it hit the expected application and every
    # changed field equals the label. Meaningless (False) otherwise.
    update_correct: bool
    origin: str = "rules"
    reason: str = ""


def wilson_interval(
    successes: int, total: int, z: float = 1.96
) -> tuple[float, float] | None:
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


@dataclass(frozen=True)
class Rate:
    successes: int
    total: int

    @property
    def value(self) -> float | None:
        return self.successes / self.total if self.total else None

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson_interval(self.successes, self.total)

    def display(self) -> str:
        if self.total == 0:
            return "unmeasured"
        low, high = self.interval
        return (
            f"{self.value:.1%} ({self.successes}/{self.total}, "
            f"95% CI {low:.1%}-{high:.1%})"
        )

    def to_dict(self) -> dict:
        interval = self.interval
        return {
            "successes": self.successes,
            "total": self.total,
            "value": self.value,
            "ci_low": interval[0] if interval else None,
            "ci_high": interval[1] if interval else None,
            "status": "measured" if self.total else "unmeasured",
        }


def _is_correct_update(result: ScenarioResult) -> bool:
    return (
        result.actual == "applied"
        and result.expected == "applied"
        and result.update_correct
    )


def precision(results: list[ScenarioResult]) -> Rate:
    """Correct automatic updates over all automatic updates."""
    updates = [r for r in results if r.actual == "applied"]
    return Rate(sum(_is_correct_update(r) for r in updates), len(updates))


def coverage(results: list[ScenarioResult]) -> Rate:
    """Share of scenarios handled without review."""
    return Rate(sum(r.actual in {"applied", "ignored"} for r in results), len(results))


def detection_recall(results: list[ScenarioResult]) -> Rate:
    """Relevant mail that was updated correctly or sent to review."""
    relevant = [r for r in results if r.expected in RELEVANT]
    hits = sum(
        r.actual == "review_required" or _is_correct_update(r) for r in relevant
    )
    return Rate(hits, len(relevant))


def automatic_recall(results: list[ScenarioResult]) -> Rate:
    expected = [r for r in results if r.expected == "applied"]
    return Rate(sum(_is_correct_update(r) for r in expected), len(expected))
