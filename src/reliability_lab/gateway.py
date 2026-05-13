from __future__ import annotations

import time
from dataclasses import dataclass

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError
from reliability_lab.providers import FakeLLMProvider, ProviderError, ProviderResponse


@dataclass(slots=True)
class GatewayResponse:
    text: str
    route: str
    provider: str | None
    cache_hit: bool
    latency_ms: float
    estimated_cost: float
    error: str | None = None


class ReliabilityGateway:
    """Routes requests through cache, circuit breakers, and fallback providers."""

    def __init__(
        self,
        providers: list[FakeLLMProvider],
        breakers: dict[str, CircuitBreaker],
        cache: ResponseCache | SharedRedisCache | None = None,
    ):
        self.providers = providers
        self.breakers = breakers
        self.cache = cache

    def complete(self, prompt: str) -> GatewayResponse:
        """Return a reliable response or a static fallback.

        TODO(student): Improve route reasons, cache safety checks, and error handling.
        TODO(student): Add cost budget check - if cumulative cost exceeds a threshold,
        skip expensive providers and route to cache or cheaper fallback.
        """
        started = time.perf_counter()

        def elapsed_ms() -> float:
            return (time.perf_counter() - started) * 1000

        if self.cache is not None:
            try:
                cached, score = self.cache.get(prompt)
                if cached is not None:
                    return GatewayResponse(
                        text=cached,
                        route=f"cache_hit:{score:.2f}",
                        provider=None,
                        cache_hit=True,
                        latency_ms=elapsed_ms(),
                        estimated_cost=0.0,
                    )
            except Exception:
                # Cache failures must not break primary serving path.
                pass

        last_error: str | None = None
        for index, provider in enumerate(self.providers):
            breaker = self.breakers[provider.name]
            try:
                response: ProviderResponse = breaker.call(provider.complete, prompt)
                if self.cache is not None:
                    try:
                        self.cache.set(prompt, response.text, {"provider": provider.name})
                    except Exception:
                        pass
                route = f"primary:{provider.name}" if index == 0 else f"fallback:{provider.name}"
                return GatewayResponse(
                    text=response.text,
                    route=route,
                    provider=provider.name,
                    cache_hit=False,
                    latency_ms=elapsed_ms(),
                    estimated_cost=response.estimated_cost,
                )
            except (ProviderError, CircuitOpenError) as exc:
                last_error = f"{provider.name}: {exc}"
                continue

        return GatewayResponse(
            text="The service is temporarily degraded. Please try again soon.",
            route="static_fallback",
            provider=None,
            cache_hit=False,
            latency_ms=elapsed_ms(),
            estimated_cost=0.0,
            error=last_error,
        )
