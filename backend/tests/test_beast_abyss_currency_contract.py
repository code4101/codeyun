"""Currency policy wiring and fail-closed parsing; no simulated game actions."""
import pytest

from backend.core.fanxiu.activity.beast_abyss import read_beast_abyss_currency_snapshot
from backend.core.fanxiu.instrumentation import wallet
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


@pytest.mark.parametrize('allow_discovery', [False, True])
@pytest.mark.parametrize('amount', [0, 125])
def test_beast_currency_uses_only_type14_client_semantics(monkeypatch, allow_discovery, amount):
    calls = []
    native = {'exchange_currency': amount, 'cumulative_currency': amount + 10,
              'source': 'runtime_memory', 'evidence': {'pid': 3412}}

    def read(currency_type, **kwargs):
        calls.append((currency_type, kwargs))
        return native

    monkeypatch.setattr(wallet, 'read_wallet_currency_snapshot', read)
    result = read_beast_abyss_currency_snapshot(allow_discovery=allow_discovery)
    assert calls == [(14, {'allow_discovery': allow_discovery, 'missing_as_zero': True})]
    assert result['exchange_currency'] == amount
    assert result['cumulative_currency'] == amount + 10
    assert result['evidence']['pid'] == 3412
    assert 'absent_entry_policy' in result['evidence']
    assert native['evidence'] == {'pid': 3412}


@pytest.mark.parametrize('manager', [
    {'inst': {'Model': {'WalletData': {}}}},
    {'inst': {'Model': {'WalletData': {'_WalletInfo': {
        14: {'type': 19, 'amount': 8, 'borrow': 0, 'history': 8},
    }}}}},
])
def test_zero_policy_rejects_uninitialized_or_invalid_present_wallet(monkeypatch, manager):
    class Reader:
        def fields(self, value):
            return value

        def dictionary_fields(self, value):
            return value

        def long(self, value):
            return value

    monkeypatch.setattr(wallet, 'manager_index_fields', lambda *_: manager)
    with pytest.raises(FanxiuRuntimeMemoryError):
        wallet.wallet_currency_data(Reader(), 123, 14, missing_as_zero=True)


def test_beast_currency_does_not_swallow_permission_failure(monkeypatch):
    error = PermissionError('denied')

    def read(*args, **kwargs):
        raise error

    monkeypatch.setattr(wallet, 'read_wallet_currency_snapshot', read)
    with pytest.raises(PermissionError) as exc:
        read_beast_abyss_currency_snapshot()
    assert exc.value is error
