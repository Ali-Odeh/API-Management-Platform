# Load test (k6)

How much time does the gateway add to each request? `gateway.js` sends the
same requests at the same fixed rate three times, one run after the other:

| Run | Path | Shows |
| --- | --- | --- |
| `direct` | k6 → mock backend | the baseline: the backend alone |
| `gateway_public` | k6 → gateway → mock backend (public route) | the gateway itself: routing, logging, the workspace's rate limit (one Redis call, since MT.8), proxying, one more network hop |
| `gateway_protected` | k6 → gateway → mock backend (key + rate limit) | plus the API key check; the consumer's bucket rides in the same Redis call |

The differences between the runs are what the gateway costs. Before the runs,
the script creates its own service, routes, consumer and key through the
admin API (`lt-<run id>-...`), and deletes them afterwards.

All of them live in the `default` workspace, whose own rate limit (6,000
requests/min by default, MT.8) would refuse most of 300 requests/s. So the
Platform Admin logs in with password **and MFA** (the script enrolls a TOTP
factor named `loadtest` through GoTrue, like the e2e tests do), raises
`default`'s limit for the test, and afterwards puts back what was there and
removes the factor. That is why the runner also gets
`GOTRUE_SERVICE_ROLE_KEY`.

## Run it

With the stack up (`docker compose up -d`), from the repo root:

```powershell
docker compose run --rm loadtest
```

That is 300 requests/s for 30 s per run, about 1 min 40 s in total. Another
rate or duration:

```powershell
docker compose run --rm -e RATE=500 -e DURATION=60 loadtest
```

k6 runs in Docker, inside the Compose network, so nothing needs installing.
The full k6 data of the last run is saved in `loadtest/results/summary.json`
(not committed).

## The gateway's own view

k6 measures from the outside. The gateway also logs, for every request, how
long it took in total (`latency_ms`), how long it waited for the backend
(`upstream_ms`) and the difference, its own work (`gateway_ms`). For the run
id k6 prints at the end:

```powershell
docker compose logs gateway --no-log-prefix --since 10m | py loadtest/gateway_ms.py <run id>
```

## Reading the results

- **p95**: 95% of requests were faster than this. The usual number to quote,
  because an average hides the slow requests.
- **Thresholds** make the run fail (exit code not 0): more than 1% errors,
  a p95 above 50 ms (a safety net, not a target), or more than 1% of the
  planned requests not sent.
- **One machine:** k6, the gateway, Redis and the backend all share the same
  laptop in Docker Desktop. The absolute numbers depend on that machine; the
  differences between the runs are the meaningful part.
- **The rate** stays well below what the mock backend can handle (it keeps a
  p95 under 2 ms at 1,000 requests/s), so requests don't queue in the backend
  and get counted as gateway time.

## Results with workspace limits (Nov 22, 2026, MT.8)

300 requests/s, 30 s per run, about 9,000 requests per run, 0 errors:

| Run | median | p95 | p99 |
| --- | --- | --- | --- |
| direct | 1.02 ms | 1.71 ms | 3.20 ms |
| gateway_public | 1.83 ms | 2.57 ms | 3.11 ms |
| gateway_protected | 1.80 ms | 2.53 ms | 3.24 ms |

**The gateway adds about 0.8 ms (median and p95)**, all included, as before.
Public and protected routes now cost the same: both make the one Redis call
(the workspace's bucket, plus the consumer's on a protected route, in the
same script). The gateway's own log for the run:

| Route | `gateway_ms` median | p95 |
| --- | --- | --- |
| public (route, log, workspace bucket, proxy) | 0.38 ms | 0.56 ms |
| protected (+ key check, consumer bucket) | 0.38 ms | 0.56 ms |

## Results (Nov 19, 2026, Docker Desktop on Windows, MVP)

300 requests/s, 30 s per run, about 9,000 requests per run, 0 errors:

| Run | median | p95 | p99 |
| --- | --- | --- | --- |
| direct | 0.90 ms | 1.39 ms | 2.62 ms |
| gateway_public | 1.39 ms | 1.99 ms | 2.47 ms |
| gateway_protected | 1.68 ms | 2.34 ms | 3.03 ms |

**The gateway adds about 0.8 ms (median) and 1 ms (p95)**, all included.

The gateway's own log for the same run splits that up:

| Route | `gateway_ms` median | p95 |
| --- | --- | --- |
| public (route, log, proxy) | 0.075 ms | 0.12 ms |
| protected (+ key check, Redis) | 0.34 ms | 0.50 ms |

So the gateway's own code takes under 0.1 ms, the Redis rate-limit call adds
about 0.3 ms, and the rest of what k6 sees is the extra network hop
(k6 → gateway → backend instead of k6 → backend).
