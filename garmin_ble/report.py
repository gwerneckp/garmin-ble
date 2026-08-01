"""Results and rendering for :meth:`~garmin_ble.watch.Watch.collect`.

"Wait for one sample of each of these, then tell me what turned up and what did
not" is the shape of every smoke test and every first-run check, so the
bookkeeping for it lives here rather than in each caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from .metrics.base import Metric, Reading

CHECK = "✅"
WAITING = "⏳"
BLOCKED = "⚠️"


@dataclass(frozen=True)
class Missing:
    """Why one requested metric produced nothing.

    The distinction matters: an unsupported metric will never arrive no matter
    how long you wait, whereas a supported one may just need movement.
    """

    metric: Metric
    reason: str
    supported: bool = True

    def __str__(self) -> str:
        return self.reason


@dataclass
class CollectionResult:
    """What :meth:`~garmin_ble.watch.Watch.collect` gathered.

    ``samples`` holds the most recent reading per metric and ``counts`` how many
    arrived, so a caller can tell "one lucky packet" from "streaming steadily".
    """

    requested: Sequence[Metric]
    samples: Dict[Metric, Reading] = field(default_factory=dict)
    counts: Dict[Metric, int] = field(default_factory=dict)
    missing: Dict[Metric, Missing] = field(default_factory=dict)
    elapsed: float = 0.0
    timed_out: bool = False

    @property
    def complete(self) -> bool:
        """True when every requested metric produced at least one sample."""
        return not self.missing

    @property
    def received(self) -> List[Metric]:
        return [m for m in self.requested if m in self.samples]

    def __bool__(self) -> bool:
        return self.complete

    def __getitem__(self, metric: Metric) -> Reading:
        return self.samples[metric]

    def get(self, metric: Metric) -> Optional[Reading]:
        return self.samples.get(metric)

    def __str__(self) -> str:
        return Checklist.from_collection(self)


class Checklist:
    """Renders a metric checklist as plain text.

    Kept separate from :class:`CollectionResult` so the data stays printable
    without committing the library to one presentation.
    """

    @staticmethod
    def render(
        rows: Iterable["tuple[str, bool, str]"],
        indent: str = "    ",
    ) -> str:
        """Render ``(label, done, detail)`` rows as a checklist block."""
        lines = []
        for label, done, detail in rows:
            mark = CHECK if done else WAITING
            lines.append(f"{indent}{mark}  {label}")
            if detail:
                lines.append(f"{indent}    {detail}")
        return "\n".join(lines)

    @classmethod
    def from_collection(cls, result: CollectionResult, indent: str = "    ") -> str:
        body = []
        for metric in result.requested:
            reading = result.samples.get(metric)
            if reading is not None:
                body.append(f"{indent}{CHECK}  {metric.name:<14} {reading}")
                continue
            miss = result.missing.get(metric)
            mark = BLOCKED if (miss and not miss.supported) else WAITING
            body.append(f"{indent}{mark}  {metric.name:<14} {miss.reason if miss else 'no data'}")
        return "\n".join(body)

    @classmethod
    def from_flags(cls, flags: Mapping[str, bool], indent: str = "    ") -> str:
        """Render an arbitrary name-to-done mapping in the same style."""
        return "\n".join(
            f"{indent}{CHECK if done else WAITING}  {label}" for label, done in flags.items()
        )


__all__ = ["CollectionResult", "Missing", "Checklist", "CHECK", "WAITING", "BLOCKED"]
