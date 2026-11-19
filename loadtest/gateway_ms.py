"""The gateway's own view of a load test run: percentiles of the times it logged.

k6 measures from the outside. The gateway also writes, for every request,
latency_ms (the whole request), upstream_ms (waiting for the backend) and
gateway_ms (the difference: its own work) to its JSON log (step 3.5). This
reads those log lines for one run and prints their percentiles, as a second
opinion on k6's numbers.

Usage (PowerShell, from the repo root; the run id is printed by k6):
    docker compose logs gateway --no-log-prefix --since 10m | py loadtest/gateway_ms.py 3f9a2c
"""

import json
import sys
from collections import defaultdict

FIELDS = ("latency_ms", "upstream_ms", "gateway_ms")


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile: the value below which p% of the samples fall."""
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(p / 100 * len(ordered)) - 1))
    return ordered[index]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    prefix = f"/lt-{sys.argv[1]}/"

    # route label ("public" / "protected") -> field -> values
    samples: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for line in sys.stdin:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # not a JSON log line
        path = entry.get("path", "")
        if entry.get("msg") != "request" or not path.startswith(prefix):
            continue
        label = path[len(prefix) :].split("/", 1)[0]
        for field in FIELDS:
            samples[label][field].append(float(entry[field]))

    if not samples:
        sys.exit(f"no request log lines for run {sys.argv[1]} (wrong id, or --since too short?)")

    print(f"\nGateway log, run {sys.argv[1]} (ms)\n")
    print(f"{'route':<11}{'requests':>9}  {'field':<12}{'median':>8}{'p95':>8}{'p99':>8}")
    for label in sorted(samples):
        for i, field in enumerate(FIELDS):
            values = samples[label][field]
            name = label if i == 0 else ""
            count = str(len(values)) if i == 0 else ""
            p50, p95, p99 = (percentile(values, p) for p in (50, 95, 99))
            print(f"{name:<11}{count:>9}  {field:<12}{p50:>8.3f}{p95:>8.3f}{p99:>8.3f}")
    print()


if __name__ == "__main__":
    main()
