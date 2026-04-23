"""Per-provider circuit breaker + health score."""
import time
from dataclasses import dataclass, field
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    HALF_OPEN = "HALF_OPEN"
    OPEN = "OPEN"


DEFAULT_CIRCUIT = {
    "failure_threshold": 5,
    "failure_window_sec": 60,
    "cooldown_sec": 60,
    "cooldown_max_sec": 600,
    "half_open_probe": 10,
    "half_open_success_rate": 0.8,
}

DEFAULT_HEALTH = {
    "success_delta": 1,
    "failure_delta": -5,
    "timeout_delta": -10,
    "rate_limit_delta": -15,
    "floor": 0,
    "ceiling": 100,
    "half_open_threshold": 30,
    "force_open_threshold": 10,
}


@dataclass
class CircuitBreaker:
    name: str
    circuit_cfg: dict = field(default_factory=lambda: dict(DEFAULT_CIRCUIT))
    health_cfg: dict = field(default_factory=lambda: dict(DEFAULT_HEALTH))

    state: CircuitState = CircuitState.CLOSED
    failures: list[float] = field(default_factory=list)
    health_score: int = 100
    cooldown_until: float = 0.0
    cooldown_sec: float = 0.0
    half_open_attempts: int = 0
    half_open_successes: int = 0

    def _cfg(self, name: str, default_key: str | None = None):
        return self.circuit_cfg.get(name, DEFAULT_CIRCUIT.get(name))

    def _hcfg(self, name: str):
        return self.health_cfg.get(name, DEFAULT_HEALTH.get(name))

    def can_pass(self) -> bool:
        now = time.time()
        if self.state == CircuitState.OPEN:
            if now >= self.cooldown_until:
                self.state = CircuitState.HALF_OPEN
                self.half_open_attempts = 0
                self.half_open_successes = 0
            else:
                return False
        return True

    def _clip(self, score: int) -> int:
        return max(self._hcfg("floor"), min(self._hcfg("ceiling"), score))

    def record_success(self) -> None:
        self.health_score = self._clip(self.health_score + self._hcfg("success_delta"))
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_attempts += 1
            self.half_open_successes += 1
            probe = self._cfg("half_open_probe")
            rate = self._cfg("half_open_success_rate")
            if self.half_open_attempts >= probe:
                if self.half_open_successes / self.half_open_attempts >= rate:
                    self._close()
                else:
                    self._open(extend=True)
        else:
            self.failures.clear()

    def record_failure(self, kind: str = "error") -> None:
        delta_map = {
            "timeout": self._hcfg("timeout_delta"),
            "rate_limit": self._hcfg("rate_limit_delta"),
        }
        delta = delta_map.get(kind, self._hcfg("failure_delta"))
        self.health_score = self._clip(self.health_score + delta)

        now = time.time()
        window = self._cfg("failure_window_sec")
        self.failures = [t for t in self.failures if now - t <= window]
        self.failures.append(now)

        if self.state == CircuitState.HALF_OPEN:
            self.half_open_attempts += 1
            self._open(extend=True)
            return

        if self.state == CircuitState.CLOSED:
            threshold = self._cfg("failure_threshold")
            if len(self.failures) >= threshold or self.health_score <= self._hcfg(
                "force_open_threshold"
            ):
                self._open(extend=False)

    def _close(self) -> None:
        self.state = CircuitState.CLOSED
        self.failures.clear()
        self.cooldown_sec = 0
        self.cooldown_until = 0
        self.half_open_attempts = 0
        self.half_open_successes = 0

    def _open(self, *, extend: bool) -> None:
        base = self._cfg("cooldown_sec")
        max_cd = self._cfg("cooldown_max_sec")
        if extend and self.cooldown_sec > 0:
            self.cooldown_sec = min(self.cooldown_sec * 2, max_cd)
        else:
            self.cooldown_sec = base
        self.state = CircuitState.OPEN
        self.cooldown_until = time.time() + self.cooldown_sec

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "health": self.health_score,
            "failures_in_window": len(self.failures),
            "cooldown_sec": self.cooldown_sec,
            "cooldown_remaining": max(0.0, self.cooldown_until - time.time()),
        }
