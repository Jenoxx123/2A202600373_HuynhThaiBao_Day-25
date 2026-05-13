# Day 10 Reliability Report

## 1. Architecture summary

Gateway handles requests in this order: cache lookup first, then provider chain protected by per-provider circuit breakers, then static fallback when all providers fail. Route reason includes source detail (for example `cache_hit:0.93`, `primary:primary`, `fallback:backup`) to improve observability.

```
User Request
    |
    v
[Gateway] ---> [Cache check] ---> HIT? return cached
    |                                 |
    v                                 v MISS
[Circuit Breaker: Primary] -------> Provider A
    |  (OPEN? skip)
    v
[Circuit Breaker: Backup] --------> Provider B
    |  (OPEN? skip)
    v
[Static fallback message]
```

## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | Open circuit quickly enough to avoid retry storm while tolerating short jitter bursts. |
| reset_timeout_seconds | 2 | Short recovery probe window for simulated provider failures. |
| success_threshold | 1 | Close quickly after one successful probe in this lab workload. |
| cache TTL | 300 | Reuse repeated FAQ/policy questions during load tests. |
| similarity_threshold | 0.92 | Reduce semantic false hits (especially year-sensitive queries). |
| load_test requests | 200 per scenario (4 scenarios) | Meets phase-6 recommendation (200+) and gives more stable percentiles. |

## 3. SLO definitions

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 99% | 98.00% | No |
| Latency P95 | < 2500 ms | 503.15 ms | Yes |
| Fallback success rate | >= 95% | 95.28% | Yes |
| Cache hit rate | >= 10% | 38.00% | Yes |
| Recovery time | < 5000 ms | 6064.00 ms | No |

## 4. Metrics

Source: `reports/metrics.json`

| Metric | Value |
|---|---:|
| total_requests | 800 |
| availability | 0.98 |
| error_rate | 0.02 |
| latency_p50_ms | 221.58 |
| latency_p95_ms | 503.15 |
| latency_p99_ms | 533.86 |
| fallback_success_rate | 0.9528 |
| cache_hit_rate | 0.38 |
| estimated_cost_saved | 0.304 |
| circuit_open_count | 42 |
| recovery_time_ms | 6064.0021324157715 |

## 5. Cache comparison

Comparison run: same config, then rerun with `cache.enabled=false`.

| Metric | Without cache | With cache | Delta |
|---|---:|---:|---|
| latency_p50_ms | 235.55 | 221.58 | -13.97 ms (~-5.93%) |
| latency_p95_ms | 507.74 | 503.15 | -4.59 ms (~-0.90%) |
| estimated_cost | 0.372340 | 0.206292 | -0.166048 (~-44.59%) |
| cache_hit_rate | 0 | 0.38 | +0.38 |

## 6. Redis shared cache

- Why in-memory cache is insufficient for multi-instance deployments: each process has isolated memory, so cache warms independently and misses increase after horizontal scaling.
- How `SharedRedisCache` solves this: all gateway instances read/write shared keys in Redis (`HSET` + `EXPIRE`), so cache state is reused across instances.

### Evidence of shared state

```
pytest -q tests/test_redis_cache.py::test_shared_state_across_instances
.                                                                        [100%]
1 passed in 0.09s
```

### Redis CLI output

```bash
docker compose exec redis redis-cli KEYS "rl:cache:*"
1) "rl:cache:8baa2cfa11fa"
2) "rl:cache:9e413fd814eb"
3) "rl:cache:b2a52f7dc795"
4) "rl:cache:095946136fea"
```

### In-memory vs Redis latency comparison (optional)

| Metric | In-memory cache | Redis cache | Notes |
|---|---:|---:|---|
| latency_p50_ms | 217.43 | 221.58 | Redis has small network/serialization overhead. |
| latency_p95_ms | 508.11 | 503.15 | Tail latency is similar in this local setup. |

## 7. Chaos scenarios

### Circuit breaker transition log sample

```
closed -> open      (reason: failure_threshold)
open -> half_open   (reason: reset_timeout_elapsed)
half_open -> closed (reason: probe_success)
```

### Scenario outcomes

| Scenario | Expected behavior | Observed behavior | Pass/Fail |
|---|---|---|---|
| primary_timeout_100 | All traffic fallback to backup, circuit opens | Fallback path dominated, breaker opened repeatedly, service still responded. | Pass |
| primary_flaky_50 | Circuit oscillates, mix of primary and fallback | Mixed routing with breaker opens/recovery and successful service continuation. | Pass |
| all_healthy | All requests via primary, no circuit opens | Healthy path stayed stable in scenario evaluation. | Pass |
| cache_stale_candidate | Detect stale/false-hit risk and avoid unsafe cache hit | Guardrails (privacy + year mismatch false-hit checks) prevented unsafe reuse. | Pass |

## 8. Failure analysis

- Remaining weakness: circuit breaker state is local per process; in multi-instance production, one instance can open while another still hammers a failing provider.
- Proposed fix: move breaker counters/state to Redis (or another shared store) with atomic updates and short TTL windows.

## 9. Next steps

1. Implement Redis-backed distributed circuit breaker state (`INCR`, TTL, and half-open probe lock).
2. Add concurrent load execution (thread pool/async) to stress fallback behavior under burst traffic.
3. Export Prometheus metrics for request counts, latency buckets, cache hits, and circuit state.
