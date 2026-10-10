"""Bounded log parser contracts; no induced production network failures."""
import json
import pytest
from backend.core.codex.desktop import read_desktop_transport_diagnostics


def test_tail_classification_never_exposes_raw_credentials_or_claims_cause(tmp_path):
    path = tmp_path / 'provider.log'
    path.write_text('secret-canary-credential\nconnect ENOENT\nerror decoding response body\n', encoding='utf-8')
    result = read_desktop_transport_diagnostics(log_path=path)
    assert result['pattern_counts']['endpoint_missing'] == 1
    assert result['pattern_counts']['response_decode_message'] == 1
    assert 'secret-canary' not in json.dumps(result)
    assert result['remote_compact_root_cause'] == 'unknown'
    assert not result['request_correlation_available'] and not result['recovery_performed']


def test_missing_log_and_partial_line_are_not_fabricated_errors(tmp_path):
    path = tmp_path / 'provider.log'
    assert read_desktop_transport_diagnostics(log_path=path)['status'] == 'missing'
    path.write_bytes(b'prefix connect ENOENT\nclean\n')
    result = read_desktop_transport_diagnostics(log_path=path, max_bytes=20)
    assert result['tail_truncated'] and result['pattern_counts']['endpoint_missing'] == 0
    assert result['bytes_read'] <= 20


@pytest.mark.parametrize('size', [0, 65537, True])
def test_invalid_log_budget_rejected(tmp_path, size):
    with pytest.raises(ValueError):
        read_desktop_transport_diagnostics(log_path=tmp_path / 'missing', max_bytes=size)
