import pytest
import requests

from backend.core.skilldep_materials import codehub

BASE = 'https://hub.example/api'
PLAN = BASE + '/data-platform/skill-dependency/plan'


class Response:
    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self.ok = status_code < 400
        self._payload = {} if payload is None else payload
        self.text = text

    def json(self):
        return self._payload


class Transport:
    """Fake ``requests.request``: records calls and replays queued responses or exceptions."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({'method': method, 'url': url, 'json': kwargs.get('json'), 'params': kwargs.get('params'),
                           'auth': kwargs['headers']['Authorization']})
        outcome = self.outcomes.pop(0) if self.outcomes else Response()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def hub(monkeypatch):
    client = object.__new__(codehub.CodeHubSkillDep)
    client._peer = {'token': 'fake-token'}
    client.api_base = BASE
    client.retries, client.backoff = 3, 0

    def install(*outcomes):
        transport = Transport(*outcomes)
        monkeypatch.setattr(codehub.requests, 'request', transport)
        return transport

    return client, install


def _relocate_body(**overrides):
    body = {'cloud_path': '/data/fixture/domain/label/sample_sd-x', 'expected_resource_version': 'v-fixture',
            'listing_verified': True}
    body.update(overrides)
    return body


@pytest.mark.parametrize('method, url', [
    ('storage_naming', PLAN + '/storage-naming'),
    ('storage_layout', PLAN + '/storage-layout'),
    ('finalizations', PLAN + '/finalizations'),
])
def test_reads_use_exact_paths(hub, method, url):
    client, install = hub
    transport = install(Response(payload={'count': 0, 'items': []}))
    assert getattr(client, method)() == {'count': 0, 'items': []}
    assert transport.calls == [{'method': 'GET', 'url': url, 'json': None, 'params': None, 'auth': 'Bearer fake-token'}]


@pytest.mark.parametrize('root', ['local', 'cloud'])
def test_put_finalization_path_and_body(hub, root):
    client, install = hub
    transport = install(Response(payload={'material_id': 'sd-x', 'root': root}))
    record = {'status': 'pending', 'path': 'F:/fixture/sample_sd-x'}
    assert client.put_finalization('sd-x', root, record)['root'] == root
    assert transport.calls[0]['method'] == 'PUT'
    assert transport.calls[0]['url'] == f'{PLAN}/materials/sd-x/finalizations/{root}'
    assert transport.calls[0]['json'] == record


@pytest.mark.parametrize('root', ['staging', '', 'Cloud', 'local/../cloud', None])
def test_put_finalization_rejects_other_roots(hub, root):
    client, install = hub
    transport = install()
    with pytest.raises(ValueError):
        client.put_finalization('sd-x', root, {'status': 'pending'})
    assert transport.calls == []


@pytest.mark.parametrize('material_id', ['', '  ', '.', '..', None, 7])
def test_identifiers_are_validated_before_sending(hub, material_id):
    client, install = hub
    transport = install()
    with pytest.raises(ValueError):
        client.put_finalization(material_id, 'local', {'status': 'pending'})
    with pytest.raises(ValueError):
        client.relocate_cloud_transfer(material_id, 'acct', _relocate_body())
    with pytest.raises(ValueError):
        client.relocate_cloud_transfer('sd-x', material_id, _relocate_body())
    assert transport.calls == []


def test_identifiers_are_quoted_as_single_segments(hub):
    client, install = hub
    transport = install(Response(), Response())
    client.put_finalization('sd/x y', 'cloud', {'status': 'pending'})
    client.relocate_cloud_transfer('sd/x', 'acct/../other', _relocate_body())
    assert transport.calls[0]['url'] == f'{PLAN}/materials/sd%2Fx%20y/finalizations/cloud'
    assert transport.calls[1]['url'] == f'{PLAN}/materials/sd%2Fx/cloud-transfers/acct%2F..%2Fother/relocate'


def test_relocate_posts_exact_body(hub):
    client, install = hub
    transport = install(Response(payload={'status': 'transferred'}))
    body = _relocate_body(observed_at='2026-10-21T00:00:00Z', note='fixture')
    assert client.relocate_cloud_transfer('sd-x', 'acct', body) == {'status': 'transferred'}
    assert transport.calls == [{'method': 'POST', 'url': f'{PLAN}/materials/sd-x/cloud-transfers/acct/relocate',
                                'json': body, 'params': None, 'auth': 'Bearer fake-token'}]


@pytest.mark.parametrize('body', [
    _relocate_body(listing_verified=False),
    _relocate_body(listing_verified='true'),
    {k: v for k, v in _relocate_body().items() if k != 'listing_verified'},
    _relocate_body(cloud_path=''),
    _relocate_body(expected_resource_version=None),
    _relocate_body(status='transferred'),
    'not-a-dict',
])
def test_relocate_rejects_invalid_body(hub, body):
    client, install = hub
    transport = install()
    with pytest.raises(ValueError):
        client.relocate_cloud_transfer('sd-x', 'acct', body)
    assert transport.calls == []


@pytest.mark.parametrize('call', [
    lambda c: c.put_finalization('sd-x', 'cloud', {'status': 'pending', 'expected_resource_version': 'old'}),
    lambda c: c.relocate_cloud_transfer('sd-x', 'acct', _relocate_body(expected_resource_version='old')),
])
def test_http_409_is_preserved_and_not_retried(hub, call):
    client, install = hub
    transport = install(Response(409, text='resource_version mismatch'))
    with pytest.raises(codehub.CodeHubError) as exc:
        call(client)
    assert exc.value.status_code == 409
    assert 'HTTP 409' in str(exc.value) and 'resource_version mismatch' in str(exc.value)
    assert len(transport.calls) == 1


@pytest.mark.parametrize('error', [requests.ConnectionError('lost'), requests.Timeout('lost')])
def test_relocate_is_submitted_once_on_lost_response(hub, error):
    client, install = hub
    transport = install(error, Response(payload={'unexpected': 'second submission'}))
    with pytest.raises(codehub.CodeHubError) as exc:
        client.relocate_cloud_transfer('sd-x', 'acct', _relocate_body())
    assert exc.value.status_code is None
    assert '未重试' in str(exc.value) and 'fake-token' not in str(exc.value)
    assert len(transport.calls) == 1


def test_put_finalization_upsert_keeps_bounded_retry(hub):
    client, install = hub
    transport = install(requests.ConnectionError('lost'), Response(payload={'ok': True}))
    assert client.put_finalization('sd-x', 'local', {'status': 'pending'}) == {'ok': True}
    assert len(transport.calls) == 2

    transport = install(*[requests.ConnectionError('lost')] * 5)
    with pytest.raises(codehub.CodeHubError):
        client.put_finalization('sd-x', 'local', {'status': 'pending'})
    assert len(transport.calls) == client.retries
