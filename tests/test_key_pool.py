from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest

from app.key_pool import KeyPool, PoolPolicy, PoolUnavailable, parse_keys, retry_delay
from app.providers import configured_provider


def reply(status=200, headers=None, **data):
    return httpx.Response(status, headers=headers, json=data,
                          request=httpx.Request('POST', 'https://example.test'))


def test_keys_trim_deduplicate_and_hide_secrets():
    assert parse_keys(' a, b,,a, ') == ('a', 'b')
    assert 'private-secret' not in repr(KeyPool(['private-secret']).states)


def test_rotation_cooldown_capacity_and_window(monkeypatch):
    now = [0]
    pool = KeyPool(['first', 'second'], PoolPolicy(rpm=2), clock=lambda: now[0])
    calls = []

    def post(url, headers, **kwargs):
        calls.append(headers['Authorization'])
        return reply(429, {'Retry-After': '10'}) if len(calls) == 1 else reply()

    monkeypatch.setattr(httpx, 'post', post)
    pool.post('https://example.test')
    pool.post('https://example.test')
    with pytest.raises(PoolUnavailable):
        pool.post('https://example.test')
    assert calls == ['Bearer first', 'Bearer second', 'Bearer second']
    now[0] = 10
    pool.post('https://example.test')
    assert calls[-1] == 'Bearer first'
    now[0] = 60
    pool.post('https://example.test')
    assert calls[-1] == 'Bearer second'  # Most remaining capacity.


def test_shared_quota_prevents_rotation(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, 'post', lambda *a, **kw: calls.append(kw) or reply(429))
    pool = KeyPool(['a', 'b'], PoolPolicy(shared_limits=True))
    with pytest.raises(PoolUnavailable):
        pool.post('https://example.test')
    assert len(calls) == 1


def test_retry_after_date_invalid_and_backoff():
    assert retry_delay('Thu, 01 Jan 1970 00:02:00 GMT', 1, 60) == 60
    assert retry_delay('0', 2, 0) == 0
    assert retry_delay('NaN', 3, 0) == 4
    assert retry_delay('invalid', 100, 0) == 60


def test_attempt_budget_auth_disable_and_non_retry_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, 'post', lambda *a, **kw: calls.append(kw) or reply(401))
    pool = KeyPool(['a', 'b', 'c'], PoolPolicy(max_attempts=2))
    with pytest.raises(PoolUnavailable):
        pool.post('https://example.test')
    assert len(calls) == 2
    assert [s.disabled for s in pool.states] == [True, True, False]
    monkeypatch.setattr(httpx, 'post', lambda *a, **kw: reply(400))
    assert pool.post('https://example.test').status_code == 400
    assert pool.states[2].in_flight == 0


def test_concurrency_is_reserved_before_network_and_released_on_error(monkeypatch):
    entered, release = Event(), Event()

    def post(*args, **kwargs):
        entered.set()
        release.wait(3)
        raise httpx.ConnectError('unavailable')

    monkeypatch.setattr(httpx, 'post', post)
    pool = KeyPool(['a'], PoolPolicy(concurrency=1))
    with ThreadPoolExecutor() as executor:
        future = executor.submit(pool.post, 'https://example.test')
        try:
            assert entered.wait(2)
            with pytest.raises(PoolUnavailable):
                pool.post('https://example.test')
        finally:
            release.set()
        with pytest.raises(httpx.ConnectError):
            future.result()
    assert pool.states[0].in_flight == 0
    assert len(pool.states[0].requests) == 1


@pytest.mark.parametrize('provider_name', ['gemini', 'openrouter', 'openai_compatible'])
def test_provider_pool_survives_recreation_and_rotates(settings, monkeypatch, provider_name):
    settings.ai_provider = provider_name
    settings.ai_api_keys = f'{provider_name}-one, {provider_name}-two'
    settings.ai_model = 'test'
    calls = []

    def post(url, headers, **kwargs):
        calls.append(headers)
        if len(calls) == 1:
            return reply(429, {'Retry-After': '60'})
        if provider_name == 'gemini':
            return reply(candidates=[{'content': {'parts': [{'text': 'Answer [S1]'}]}}])
        return reply(choices=[{'message': {'content': 'Answer [S1]'}}])

    monkeypatch.setattr(httpx, 'post', post)
    provider, _ = configured_provider(settings)
    assert provider.generate('question', 'health', []) == 'Answer [S1]'
    other, _ = configured_provider(settings)
    assert other.pool is provider.pool
    assert other.generate_general('question') == 'Answer [S1]'
    field = 'x-goog-api-key' if provider_name == 'gemini' else 'Authorization'
    assert calls[1][field].endswith('-two') and calls[2][field].endswith('-two')
