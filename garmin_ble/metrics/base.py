"""The ``Metric`` descriptor and the ``Reading`` base class.

A *metric* is the single handle for one kind of telemetry. It binds the
``GarminService`` code, the reading type, and the parser together, so
subscribing to heart rate cannot disagree with parsing heart rate.

A *reading* is one parsed sample. Every reading is a frozen dataclass with named
fields, knows which metric produced it, and carries the time it arrived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, ClassVar, Dict, Generic, Optional, Tuple, Type, TypeVar

from ..constants import GarminService


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


class Reading:
    """Base class for one parsed telemetry sample.

    Subclasses are frozen dataclasses that add their own named fields. This base
    is deliberately *not* a dataclass: a base-class field with a default would
    force every subclass field to have one too, which would make
    ``HeartRate()`` with no arguments legal. Instead ``at`` is kept off the
    field list and stamped in ``__post_init__``.

    ``at`` is arrival time, not sensor time — the Garmin real-time protocol does
    not timestamp most telemetry.

    A subclass that needs its own ``__post_init__`` must call ``super()``'s.
    """

    #: Set on each subclass by :func:`register`; identifies the producing metric.
    METRIC: ClassVar["Metric"]

    __slots__ = ("_at",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_at", _now())

    @property
    def at(self) -> datetime:
        """When this sample was received."""
        return self._at  # type: ignore[attr-defined]

    @property
    def metric(self) -> "Metric":
        """The :class:`Metric` this reading came from."""
        return type(self).METRIC


R = TypeVar("R", bound=Reading)


@dataclass(frozen=True)
class Metric(Generic[R]):
    """One kind of telemetry: its service code, its reading type, its parser.

    Metrics are singletons — compare and hash by ``name``, so they work as dict
    keys and set members. Never construct one directly outside this package;
    use the constants in :mod:`garmin_ble.metrics`.
    """

    name: str
    service: GarminService
    reading_type: Type[R] = field(compare=False, repr=False)
    parse: Callable[[bytes], Optional[R]] = field(compare=False, repr=False)
    encode: Callable[[R], bytes] = field(compare=False, repr=False)
    description: str = field(default="", compare=False, repr=False)

    #: Every telemetry metric, in a stable order. Populated by the package
    #: ``__init__`` once all modules have been imported.
    ALL_TELEMETRY: ClassVar[Tuple["Metric", ...]] = ()

    def __str__(self) -> str:
        return self.name

    def __hash__(self) -> int:
        return hash(self.name)


_BY_SERVICE: Dict[int, Metric] = {}
_BY_NAME: Dict[str, Metric] = {}


def register(
    name: str,
    service: GarminService,
    reading_type: Type[R],
    parse: Callable[[bytes], Optional[R]],
    encode: Callable[[R], bytes],
    description: str = "",
) -> Metric[R]:
    """Create a metric, link it to its reading type, and add it to the registry.

    Called once per module in this package at import time. Linking is
    bidirectional: the metric knows its reading type and the reading type knows
    its metric, which is what makes ``reading.metric`` work without the caller
    passing anything around.

    Requiring *encode* alongside *parse* keeps the two halves of each wire
    format in one file and makes every parser round-trippable — which is what
    the simulator and the parser tests are built on.
    """
    metric: Metric[R] = Metric(
        name=name,
        service=service,
        reading_type=reading_type,
        parse=parse,
        encode=encode,
        description=description,
    )
    reading_type.METRIC = metric
    _BY_SERVICE[int(service)] = metric
    _BY_NAME[name] = metric
    return metric


def by_service(service_code: int) -> Optional[Metric]:
    """Look up the metric for a raw MLR service code, or ``None``."""
    return _BY_SERVICE.get(int(service_code))


def by_name(name: str) -> Optional[Metric]:
    """Look up a metric by its canonical name, or ``None``."""
    return _BY_NAME.get(name)


def service_label(service_code: int) -> str:
    """Display name for a raw service code.

    Falls back to the ``GarminService`` member name for control services like
    GFDI that carry no telemetry, and to the bare number for anything unknown.
    """
    metric = by_service(service_code)
    if metric is not None:
        return metric.name
    try:
        return GarminService(service_code).name.lower()
    except ValueError:
        return f"service{service_code}"


def all_metrics() -> Tuple[Metric, ...]:
    """Every registered telemetry metric, in registration order."""
    return tuple(_BY_NAME.values())
