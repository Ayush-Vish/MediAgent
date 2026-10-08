"""Thread-safe, process-local credential pool for synchronous provider calls.

Reservations happen before I/O; network calls never hold the pool lock. State
survives provider reconstruction, but not process restarts. Run one API worker.
"""
from collections import deque
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
import math
from threading import RLock
import time

import httpx
from app.observability import event, span


class PoolUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('No provider credentials currently available')


def parse_keys(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(key.strip() for key in value.split(',') if key.strip()))


@dataclass(frozen=True)
class PoolPolicy:
    rpm: int = 60
    concurrency: int = 2
    max_attempts: int = 3
    shared_limits: bool = False

    def __post_init__(self):
        if min(self.rpm, self.concurrency, self.max_attempts) < 1:
            raise ValueError('Pool limits must be positive')


@dataclass
class KeyState:
    key: str = field(repr=False)
    requests: deque = field(default_factory=deque)
    cooldown_until: float = 0
    failures: int = 0
    in_flight: int = 0
    disabled: bool = False


def retry_delay(value, failures, wall_time):
    """Support Retry-After seconds and HTTP dates, otherwise bounded backoff."""
    if value is not None:
        try:
            delay = float(value)
        except (ValueError, TypeError):
            try:
                delay = parsedate_to_datetime(value).timestamp() - wall_time
            except (ValueError, TypeError, OverflowError):
                delay = float('nan')
        if math.isfinite(delay):
            return max(0, delay)
    return min(60, 2 ** min(max(0, failures - 1), 6))


class KeyPool:
    def __init__(self, keys, policy=None, *, clock=time.monotonic, wall_clock=time.time):
        self.states = [KeyState(key) for key in dict.fromkeys(keys) if key]
        self.policy = policy or PoolPolicy()
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = RLock()
        self.requests = deque()
        self.cooldown_until = 0

    def _reserve(self, tried):
        with self.lock:
            now = self.clock()
            for queue in [self.requests, *(state.requests for state in self.states)]:
                while queue and queue[0] <= now - 60:
                    queue.popleft()
            if self.policy.shared_limits and (
                now < self.cooldown_until or len(self.requests) >= self.policy.rpm
                or sum(s.in_flight for s in self.states) >= self.policy.concurrency
            ):
                raise PoolUnavailable()
            available = [s for i, s in enumerate(self.states) if i not in tried
                         and not s.disabled and s.cooldown_until <= now
                         and len(s.requests) < self.policy.rpm
                         and s.in_flight < self.policy.concurrency]
            if not available:
                raise PoolUnavailable()
            state = min(available, key=lambda s: (len(s.requests), s.in_flight))
            tried.add(self.states.index(state))
            state.requests.append(now)
            self.requests.append(now)
            state.in_flight += 1
            return state

    def post(self, url, *, headers=None, auth_header='Authorization', auth_prefix='Bearer ', stream_format=None, **kwargs):
        tried = set()
        for _ in range(min(len(self.states), self.policy.max_attempts)):
            try:
                state = self._reserve(tried)
            except PoolUnavailable:
                event('llm.keys.unavailable', attempted=len(tried), key_count=len(self.states))
                raise
            event('llm.key.reserved', key_slot=self.states.index(state), attempt=len(tried))
            try:
                with span('llm.http', key_slot=self.states.index(state)):
                    authenticated_headers = {**(headers or {}), auth_header: auth_prefix + state.key}
                    if stream_format:
                        from app.streaming import provider_stream
                        response = provider_stream(url, authenticated_headers, stream_format, **kwargs)
                    else:
                        response = httpx.post(url, headers=authenticated_headers, **kwargs)
                event('llm.http.response', status=response.status_code, key_slot=self.states.index(state))
                with self.lock:
                    if response.status_code == 429:
                        state.failures += 1
                        delay = retry_delay(response.headers.get('retry-after'),
                                            state.failures, self.wall_clock())
                        state.cooldown_until = self.clock() + delay
                        event('llm.key.cooldown', key_slot=self.states.index(state), seconds=delay,
                              shared=self.policy.shared_limits)
                        if self.policy.shared_limits:
                            self.cooldown_until = max(self.cooldown_until, state.cooldown_until)
                    elif response.status_code in (401, 403):
                        state.disabled = True
                        event('llm.key.disabled', key_slot=self.states.index(state), status=response.status_code)
                    elif response.is_success:
                        state.failures = 0
                if response.status_code in (429, 401, 403):
                    response.close()
                    continue
                # Do not rotate on bad payloads, server errors, or network errors.
                return response
            finally:
                with self.lock:
                    state.in_flight -= 1
        raise PoolUnavailable()


_pools = {}
_registry_lock = RLock()


def get_pool(provider, endpoint, keys, policy):
    identity = (provider, endpoint, keys, policy)
    with _registry_lock:
        if identity not in _pools:
            _pools[identity] = KeyPool(keys, policy)
        return _pools[identity]
