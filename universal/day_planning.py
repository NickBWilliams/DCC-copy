"""Daily depot loops from directed road costs; no geometric pricing fallback."""
import math
from pydantic import BaseModel, Field, model_validator
from routing import RoutingUnavailable, MILES_PER_METER


class WorkdayPolicy(BaseModel):
    target_hours: float = Field(default=8, ge=1, le=10)
    max_hours: float = Field(default=10, ge=1, le=10)
    break_minutes: float = Field(default=30, ge=0, le=120)
    default_service_minutes: float = Field(default=30, gt=0, le=600)
    start_minute: int = Field(default=480, ge=0, le=1439)

    @model_validator(mode="after")
    def limits(self):
        if self.target_hours > self.max_hours:
            raise ValueError("Daily target cannot exceed the daily maximum")
        return self


def validate_matrix(matrix, size):
    for key in ("durations", "distances"):
        rows = matrix.get(key)
        if not isinstance(rows, list) or len(rows) != size:
            raise RoutingUnavailable("Road matrix is incomplete; no daily quote calculated")
        for row in rows:
            if not isinstance(row, list) or len(row) != size or any(
                isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) or v < 0 for v in row
            ):
                raise RoutingUnavailable("Road matrix contains unreachable or invalid legs; review locations")


def build_days(stops, matrix, policy):
    """Deterministic cheapest insertion + directed 2-opt, bounded by daily time.

    Heuristic, not a proof of global optimality. Every candidate includes the
    depot return, work, a conservative break before service and venue waiting.
    A stop exceeding the target alone may use the hard ceiling, with a warning.
    """
    validate_matrix(matrix, len(stops) + 1)
    times, distances = matrix["durations"], matrix["distances"]

    def measure(order):
        path = [0, *[i + 1 for i in order], 0]
        driving = sum(times[a][b] for a, b in zip(path, path[1:]))
        meters = sum(distances[a][b] for a, b in zip(path, path[1:]))
        service = sum(stops[i]["service_minutes"] * 60 for i in order)
        clock = policy.start_minute * 60
        waiting, previous = 0, 0
        # Reserve the break at the first venue, before work, so access windows
        # are never checked against a schedule that omits the break.
        for pos, i in enumerate(order):
            stop = stops[i]
            clock += times[previous][i + 1]
            if pos == 0:
                clock += policy.break_minutes * 60
            opening = stop.get("window_start_minute")
            closing = stop.get("window_end_minute")
            wait = max(0, opening * 60 - clock) if opening is not None else 0
            waiting += wait
            clock += wait + stop["service_minutes"] * 60
            if closing is not None and clock > closing * 60 + 1e-6:
                return None
            previous = i + 1
        clock += times[previous][0]
        elapsed = clock - policy.start_minute * 60
        if clock > 86400 or elapsed > policy.max_hours * 3600 + 1e-6:
            return None
        return {"stop_order": list(order), "road_miles": round(meters * MILES_PER_METER, 2),
                "driving_hours": round(driving / 3600, 4),
                "service_hours": round(service / 3600, 4),
                "waiting_hours": round(waiting / 3600, 4),
                "break_hours": round(policy.break_minutes / 60, 4),
                "total_hours": round(elapsed / 3600, 4),
                "elapsed_seconds": elapsed, "driving_seconds": driving}

    def improve(order):
        best = measure(order)
        for _ in range(8):
            changed = False
            for a in range(len(order) - 1):
                for b in range(a + 1, len(order)):
                    candidate = order[:a] + list(reversed(order[a:b + 1])) + order[b + 1:]
                    value = measure(candidate)
                    if value and (value["elapsed_seconds"], value["driving_seconds"]) < (
                        best["elapsed_seconds"] - 1e-6, best["driving_seconds"]):
                        order, best, changed = candidate, value, True
            if not changed:
                break
        return order, best

    remaining = set(range(len(stops)))
    days = []
    while remaining:
        # Farthest first keeps distant work together instead of stranding it.
        first = max(remaining, key=lambda i: (times[0][i + 1] + times[i + 1][0], -i))
        order, current = [first], measure([first])
        if current is None:
            raise RoutingUnavailable(
                f"Stop {first + 1} cannot fit the daily maximum or venue hours with return travel. "
                "Review service time, starting location, or arrange overnight travel before quoting.")
        remaining.remove(first)
        while remaining:
            choices = []
            for i in sorted(remaining):
                for pos in range(len(order) + 1):
                    candidate = order[:pos] + [i] + order[pos:]
                    value = measure(candidate)
                    if value and value["elapsed_seconds"] <= policy.target_hours * 3600 + 1e-6:
                        choices.append((value["elapsed_seconds"] - current["elapsed_seconds"], i, pos, candidate))
            if not choices:
                break
            _, i, _, candidate = min(choices)
            order, current = improve(candidate)
            remaining.remove(i)
        order, current = improve(order)
        current["day"] = len(days) + 1
        current["above_target"] = current["elapsed_seconds"] > policy.target_hours * 3600
        current.pop("elapsed_seconds")
        current.pop("driving_seconds")
        days.append(current)
    return days
