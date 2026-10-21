"""Phase-1 web share dry-run: synthetic requests and fake tabs/contexts only."""

import json
from contextlib import contextmanager

import pytest

from backend.core.baidu_netdisk import web, web_share
from backend.core.baidu_netdisk.ledger import TransferLedger

SURL = "1SynThEtIcSuRl9"
CODE = "x7k9"
TARGET = "/CodeYunSynthetic/目标"
REQUEST = {"share_url": f"https://pan.baidu.com/s/{SURL}?pwd={CODE}", "target_dir": TARGET}
SECRETS = (SURL, SURL[1:], CODE, TARGET)


class Tab:
    def __init__(self, url="about:blank", eles=None, landing=None):
        self.url = url
        self._eles = eles or {}
        self._landing = landing
        self.visited = []
        self.clicked = []

    def get(self, url):
        self.visited.append(url)
        self.url = self._landing or url

    def ele(self, selector, timeout=0):
        return (self._eles.get(selector) or [None])[0]

    def eles(self, selector, timeout=0):
        return list(self._eles.get(selector) or [])


class Owned:
    facts = {"pid": 4242, "port": 9331, "running": True}

    def __init__(self, tab):
        self.tab = tab

    def main_tab(self):
        return self.tab


def ctx(tab):
    @contextmanager
    def context():
        yield Owned(tab)
    return context


def no_attach():
    raise AssertionError("must not attach")


def assert_no_secret(result):
    text = json.dumps(result, ensure_ascii=False)
    for secret in SECRETS:
        assert secret.lower() not in text.lower(), secret


def test_default_registry_is_unverified_and_never_guesses_success():
    assert all(v == () for v in web_share.SHARE_SELECTORS.values())
    tab = Tab()
    result = web_share.prepare_share(REQUEST, context=ctx(tab))
    assert result["outcome"] == "needs_primary_observation"
    assert {s["state"] for s in result["stages"].values()} == {"selector_unverified"}
    assert result["submitted"] is False and "next_evidence" in result
    assert tab.visited == [f"https://pan.baidu.com/s/{SURL}?pwd={CODE}"]
    assert_no_secret(result)


def test_save_not_dry_run_is_unsupported_and_touches_nothing():
    result = web_share.prepare_share(REQUEST, dry_run=False, context=no_attach)
    assert result["outcome"] == "unsupported" and result["submitted"] is False
    assert_no_secret(result)


@pytest.mark.parametrize("request_data,reason", [
    ({**REQUEST, "share_url": f"http://pan.baidu.com/s/{SURL}?pwd={CODE}"}, "invalid_share_url"),
    ({**REQUEST, "share_url": f"https://pan.baidu.com.evil.example/s/{SURL}?pwd={CODE}"}, "invalid_share_url"),
    ({**REQUEST, "share_url": f"https://pan.baidu.com/s/{SURL}"}, "invalid_share_url"),  # no code
    ({**REQUEST, "target_dir": "relative/" + TARGET}, "invalid_target_dir"),
    ({**REQUEST, "target_dir": "/"}, "target_dir_is_root"),
    ({**REQUEST, "items": ["a", "A"]}, "duplicate_items"),
    ({**REQUEST, "cookie": "BDUSS=SECRET"}, "unknown_request_field"),
    ([REQUEST], "request_not_object"),
])
def test_invalid_request_reports_reason_code_only(request_data, reason):
    result = web_share.prepare_share(request_data, context=no_attach)
    assert result == {"outcome": "invalid_request", "reason": reason, "submitted": False}


def test_duplicate_ledger_request_is_not_resubmitted(tmp_path):
    ledger = TransferLedger(tmp_path / "ledger.json")
    req = web_share.parse_request(REQUEST)
    record, created = ledger.begin("save_share", **req.identity)
    assert created and record["key"] == req.key
    result = web_share.prepare_share(REQUEST, ledger=ledger, context=no_attach)
    assert result["outcome"] == "already_recorded" and result["next"] == "verify_save_share_do_not_resubmit"
    assert_no_secret(result)


def test_failed_ledger_record_does_not_block_dry_run(tmp_path):
    ledger = TransferLedger(tmp_path / "ledger.json")
    record, _ = ledger.begin("save_share", **web_share.parse_request(REQUEST).identity)
    ledger.update(record["key"], "failed")
    assert web_share.prepare_share(REQUEST, ledger=ledger, context=ctx(Tab()))["outcome"] == "needs_primary_observation"


def test_dry_run_never_writes_ledger(tmp_path):
    ledger = TransferLedger(tmp_path / "ledger.json")
    web_share.prepare_share(REQUEST, ledger=ledger, context=ctx(Tab()))
    assert not (tmp_path / "ledger.json").exists()


def test_ledger_identity_matches_native_save_share():
    req = web_share.parse_request({**REQUEST, "share_url": f"https://pan.baidu.com/share/init?surl={SURL[1:]}&pwd={CODE}"})
    assert req.url == f"https://pan.baidu.com/s/{SURL}"
    assert set(req.identity) == {"link", "target_dir", "items"} and req.identity["items"] == "*all*"


@pytest.mark.parametrize("reason,exc", [
    ("not_owned", web.OwnershipError("not_owned")),
    ("foreign_port", web.OwnershipError("foreign_port", "port 9331 answered by another process")),
    ("browser_lock_busy", web.WebError("browser lock busy: x")),
])
def test_ownership_or_lock_failure_blocks(reason, exc):
    @contextmanager
    def context():
        raise exc
        yield

    result = web_share.prepare_share(REQUEST, context=context)
    assert result["outcome"] == "blocked" and result["reason"] == reason and result["submitted"] is False


def test_real_owned_context_refuses_foreign_port(tmp_path):
    paths = web.Paths(profile=tmp_path / "profile", state=tmp_path / "state.json")
    web.write_state(paths, {"pid": 4242, "create_time": 1000.0, "port": 9331})
    ident = {"pid": 4242, "create_time": 1000.0, "profile": web._norm_path(paths.profile), "port": 9331}

    class Foreign:
        process_id = 777

    def context():
        return web.owned_context(paths=paths, identify=lambda pid: ident, attach=lambda s, p: Foreign())

    assert web_share.prepare_share(REQUEST, context=context)["reason"] == "foreign_port"


@pytest.mark.parametrize("landing,kind", [
    ("https://passport.baidu.com/v2/?login&u=SECRET", "passport"),
    ("https://wappass.baidu.com/static/captcha/tuxing.html?ak=SECRET", "foreign"),
    ("https://pan.baidu.com/error/404.html", "other"),
    ("https://pan.baidu.com/disk/main", "disk"),
])
def test_unknown_challenge_is_not_interpreted(landing, kind):
    result = web_share.prepare_share(REQUEST, context=ctx(Tab(landing=landing)))
    assert result["outcome"] == "unknown_challenge" and result["page_kind"] == kind
    assert "SECRET" not in json.dumps(result) and "stages" not in result


def test_login_ui_on_share_page_reports_login_required():
    tab = Tab(landing=f"https://pan.baidu.com/s/{SURL}", eles={web.LOGIN_SELECTORS[0]: [object()]})
    result = web_share.prepare_share(REQUEST, context=ctx(tab))
    assert result["outcome"] == "login_required" and result["evidence"] == web.LOGIN_SELECTORS[0]


SEL = {"extraction_input": ("css:#in",), "extraction_submit": ("css:#go",),
       "target_readback": ("css:#dest",), "share_rows": ("css:.row", "css:.row2")}


def test_ambiguous_single_selector():
    tab = Tab(eles={"css:#dest": [1, 2], "css:.row": [1, 2, 3], "css:#in": [1], "css:#go": [1]})
    result = web_share.prepare_share(REQUEST, context=ctx(tab), selectors=SEL)
    assert result["outcome"] == "ambiguous_selector"
    assert result["stages"]["target_readback"] == {"state": "ambiguous", "matches": {"css:#dest": 2}}
    assert result["stages"]["share_rows"] == {"state": "observed", "selector": "css:.row", "count": 3}


def test_ambiguous_conflicting_list_selectors():
    tab = Tab(eles={"css:#dest": [1], "css:.row": [1], "css:.row2": [1, 2]})
    result = web_share.prepare_share(REQUEST, context=ctx(tab), selectors=SEL)
    assert result["stages"]["share_rows"]["state"] == "ambiguous"
    assert result["outcome"] == "ambiguous_selector"


def test_injected_observed_selectors_reach_dry_run_ready_without_clicking():
    tab = Tab(eles={"css:#dest": [1], "css:.row": [1, 2]})
    result = web_share.prepare_share(REQUEST, context=ctx(tab), selectors=SEL)
    assert result["outcome"] == "dry_run_ready" and result["submitted"] is False
    assert tab.clicked == []
    assert_no_secret(result)


def test_page_fault_reports_class_only():
    class Broken(Tab):
        def get(self, url):
            raise RuntimeError(f"net error at {url}")

    result = web_share.prepare_share(REQUEST, context=ctx(Broken()))
    assert result["outcome"] == "unknown" and result["probe_error"] == "RuntimeError"
    assert_no_secret(result)


def test_redact_is_case_insensitive_and_spares_identifiers():
    req = web_share.parse_request(REQUEST)
    out = web_share.redact({"key": req.key, "note": f"opened {SURL.upper()} code {CODE}"}, req.secrets)
    assert out["key"] == req.key and out["note"] == "opened [redacted] code [redacted]"


def _refuse():
    raise web.OwnershipError("not_owned")


def test_cli_never_prints_request_values(tmp_path, capsys, monkeypatch):
    request_file = tmp_path / "req.json"
    request_file.write_text(json.dumps(REQUEST, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(web, "owned_context", _refuse)
    code = web_share.main(["prepare", "--request", str(request_file), "--ledger", str(tmp_path / "l.json")])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["reason"] == "not_owned"
    assert_no_secret(out)


def test_cli_unreadable_request(tmp_path, capsys):
    bad = tmp_path / "req.json"
    bad.write_text("{not json " + CODE, encoding="utf-8")
    assert web_share.main(["prepare", "--request", str(bad), "--ledger", str(tmp_path / "l.json")]) == 2
    assert CODE not in capsys.readouterr().out
