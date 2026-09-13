"""Authorize the local owner's thin client; no public passwordless login endpoint.

Run only with the local owner's explicit authorization. This CLI trusts OS access
to the CodeYun deployment, requires an active superuser, verifies the issued
session through normal HTTP authentication, and saves it with Windows DPAPI.
No token/password is printed; no account/password/permission is modified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://localhost:8000")
    parser.add_argument("--username", help="Only required if multiple active superusers exist")
    parser.add_argument("--expires-minutes", type=int, default=120)
    parser.add_argument("--home", type=Path, help="Thin-client profile directory")
    args = parser.parse_args()
    target = urlsplit(args.server)
    if (target.scheme != "http" or target.hostname not in {"localhost", "127.0.0.1", "::1"}
            or target.username or target.password or target.path not in {"", "/"}
            or target.query or target.fragment):
        parser.error("本机登录仅允许明确的 loopback HTTP origin")
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo.parent / "xlproject" / "src"))
    import xlproject.loadenv  # noqa: F401
    sys.path.insert(0, str(repo.parent / "fanxiu-assistant"))
    from backend.core.access.auth import create_local_owner_session
    from fanxiu_remote.storage import TokenStore, default_home, read_json, save_json
    from fanxiu_remote.transport import API
    try:
        grant = create_local_owner_session(username=args.username, expires_minutes=args.expires_minutes)
        api = API(args.server)
        api.token = grant["access_token"]
        me = api.request("GET", "/api/auth/me")
        if me.get("username") != grant["username"] or not me.get("is_superuser") or not me.get("is_active"):
            raise ValueError("实际服务端账号身份与本机授权不一致")
        capabilities = api.request("GET", "/api/fanxiu/remote/capabilities")
        home = args.home or default_home()
        TokenStore(home, api.origin).save(api.token)
        config = read_json(home / "config.json")
        save_json(home / "config.json", {**config, "server": api.origin})
        print(json.dumps({"username": me["username"], "is_superuser": True,
                          "remote_access": capabilities.get("feature_key") == "fanxiu.remote",
                          "server": api.origin, "expires_minutes": args.expires_minutes,
                          "credential_saved": True}, ensure_ascii=True))
        return 0
    except Exception as exc:
        # Provider ambiguity contains only candidate usernames; network errors
        # use the client's sanitized exception and never echo response bodies.
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
