from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from statistics import mean
from threading import Lock

from typing_extensions import TypedDict

_MAX_OBSERVATIONS = 10_000


@dataclass(frozen=True)
class _MetricKey:
    name: str
    labels: tuple[tuple[str, str], ...]


class CounterSnapshot(TypedDict):
    name: str
    labels: dict[str, str]
    value: float


class ObservationSnapshot(TypedDict):
    name: str
    labels: dict[str, str]
    count: int
    min: float
    max: float
    avg: float
    sum: float


class MetricsSnapshot(TypedDict):
    counters: list[CounterSnapshot]
    observations: list[ObservationSnapshot]


class MetricsRegistry:
    def __init__(self, max_observations: int = _MAX_OBSERVATIONS) -> None:
        self._lock = Lock()
        self._counters: dict[_MetricKey, float] = defaultdict(float)
        self._observations: dict[_MetricKey, deque[float]] = {}
        self._max_observations = max_observations

    def increment(self, name: str, amount: float = 1.0, **labels: str) -> None:
        key = _MetricKey(name=name, labels=tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] += amount

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = _MetricKey(name=name, labels=tuple(sorted(labels.items())))
        with self._lock:
            if key not in self._observations:
                self._observations[key] = deque(maxlen=self._max_observations)
            self._observations[key].append(value)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            sorted_counters = sorted(
                self._counters.items(),
                key=lambda item: (item[0].name, item[0].labels),
            )
            counters: list[CounterSnapshot] = []
            for key, value in sorted_counters:
                counters.append(
                    {
                        "name": key.name,
                        "labels": dict(key.labels),
                        "value": value,
                    }
                )
            sorted_observations = sorted(
                self._observations.items(),
                key=lambda item: (item[0].name, item[0].labels),
            )
            observations: list[ObservationSnapshot] = []
            for key, values in sorted_observations:
                if not values:
                    continue
                observations.append(
                    {
                        "name": key.name,
                        "labels": dict(key.labels),
                        "count": len(values),
                        "min": min(values),
                        "max": max(values),
                        "avg": mean(values),
                        "sum": sum(values),
                    }
                )
        return {"counters": counters, "observations": observations}

    def render_prometheus(self) -> str:
        lines: list[str] = []
        snapshot = self.snapshot()
        counters = snapshot["counters"]
        observations = snapshot["observations"]
        emitted_types: set[str] = set()
        for counter in counters:
            if counter["name"] not in emitted_types:
                lines.append(f"# HELP {counter['name']} Counter metric")
                lines.append(f"# TYPE {counter['name']} counter")
                emitted_types.add(counter["name"])
            lines.append(
                f"{counter['name']}{self._format_labels(counter['labels'])} {counter['value']}"
            )
        for observation in observations:
            base = observation["name"]
            labels = self._format_labels(observation["labels"])
            if base not in emitted_types:
                lines.append(f"# HELP {base}_total Observation count and sum")
                lines.append(f"# TYPE {base}_total counter")
                emitted_types.add(base)
            lines.append(f"{base}_count{labels} {observation['count']}")
            lines.append(f"{base}_sum{labels} {observation['sum']}")
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _format_labels(labels: dict[str, str]) -> str:
        if not labels:
            return ""
        serialized = ",".join(
            f'{key}="{MetricsRegistry._escape_label_value(value)}"'
            for key, value in sorted(labels.items())
        )
        return "{" + serialized + "}"

    @staticmethod
    def _escape_label_value(value: str) -> str:
        return value.replace("\\", r"\\").replace("\n", r"\n").replace('"', r"\"")


metrics = MetricsRegistry()
