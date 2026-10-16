"""JSON CLI around backend.core.baidu_netdisk for GUI-driven official-client transfers.

``upload`` / ``download`` drive the official client end to end through the
public window-projection + OCR services (see backend.core.baidu_netdisk.client);
the other subcommands are single steps.  Every subcommand prints one JSON object.
``upload`` only proves the exact name appeared remotely (``remote_present``); remote
content is never compared.  A download is content-verified only with ``--sha256``.
A download that returned ``submitted`` is continued with ``resume --key`` (no GUI).
``download-share`` is EXPERIMENTAL and has not been verified end to end.
GUI subcommands hold the gui:desktop:local lease: pass ``--lease-token`` to reuse
an existing grant, otherwise one is acquired for this run and released after.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.baidu_netdisk import (  # noqa: E402
    TransferLedger,
    collect_download,
    describe_upload_source,
    load_manifest,
    probe_client_version,
    snapshot_dir,
    write_manifest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version", help="官方本地桥 GetVersion 健康检查（只读，不重试）")

    p = sub.add_parser("snapshot", help="提交下载前记录官方下载目录清单")
    p.add_argument("--root", required=True, help="官方客户端实际下载目录")
    p.add_argument("--out", required=True, help="清单 JSON 输出路径")

    p = sub.add_parser("collect", help="单步识别本次新下载文件并迁移到业务目录（不覆盖）")
    p.add_argument("--manifest", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--size", required=True, type=int)
    p.add_argument("--target-dir", required=True)
    p.add_argument("--sha256")
    p.add_argument("--stable-token", help="上一次 collect 返回的 stable_token")

    p = sub.add_parser("describe-source", help="校验上传源和网盘目录，输出大小与哈希")
    p.add_argument("--path", required=True)
    p.add_argument("--remote-dir", required=True)

    p = sub.add_parser("ledger-begin", help="登记一次提交；created=false 时禁止再次提交")
    p.add_argument("--ledger", required=True)
    p.add_argument("--op", required=True, choices=["upload", "download"])
    p.add_argument("--inputs", required=True, help="JSON 对象：显式业务输入")

    p = sub.add_parser("ledger-update", help="更新提交状态")
    p.add_argument("--ledger", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--state", required=True, choices=["submitted", "observed", "verified", "failed", "abandoned"])
    p.add_argument("--facts", default="{}", help="JSON 对象")

    def gui_args(p):
        p.add_argument("--ledger", help="提交账本 JSON；默认 <data_dir>/baidu_netdisk/ledger.json")
        p.add_argument("--lease-token", help="复用已取得的 gui:desktop:local 租约 token")
        p.add_argument("--owner", default="baidu-netdisk-transfer", help="自行取租约时的任务标识")

    p = sub.add_parser("upload", help="经官方客户端上传一个本地文件到网盘目录（一次提交，不重试）；"
                                     "成功仅表示远端出现同名文件，不校验远端内容")
    p.add_argument("--path", required=True)
    p.add_argument("--remote-dir", required=True)
    gui_args(p)

    p = sub.add_parser("download", help="经官方客户端下载一个网盘文件并迁移到业务目录（不覆盖）")
    p.add_argument("--remote-path", required=True)
    p.add_argument("--local-dir", required=True)
    p.add_argument("--client-download-dir", required=True, help="官方客户端当前配置的下载目录")
    p.add_argument("--size", required=True, type=int)
    p.add_argument("--sha256", help="期望 SHA-256；缺省时仅按文件名与大小收取，content_verified=false")
    gui_args(p)

    share_help = ("【实验性，未经真实端到端验证】经官方客户端打开分享链接，勾选根目录单个文件并下载（不覆盖）。"
                  "唤起协议与表格几何仅来自官方静态主程序，结果须人工复核")
    p = sub.add_parser("download-share", help=share_help, description=share_help)
    p.add_argument("--url", required=True, help="https://pan.baidu.com/s/<surl>，可带 ?pwd=")
    p.add_argument("--pwd-env", help="从该环境变量读取 4 位提取码（避免出现在进程参数中）")
    p.add_argument("--item", required=True, help="分享根目录下的文件名")
    p.add_argument("--local-dir", required=True)
    p.add_argument("--client-download-dir", required=True, help="官方客户端当前配置的下载目录")
    p.add_argument("--size", required=True, type=int)
    p.add_argument("--sha256")
    gui_args(p)

    p = sub.add_parser("resume", help="续收已提交的下载：只读文件系统并按账本记录迁移，不发 GUI 输入、不重提")
    p.add_argument("--key", required=True)
    p.add_argument("--ledger", help="默认 <data_dir>/baidu_netdisk/ledger.json")
    p.add_argument("--attempts", type=int, default=10, help="文件系统观察次数")
    p.add_argument("--interval", type=float, default=1.0, help="两次观察间隔秒数")

    p = sub.add_parser("status", help="只读：按 key 读取账本状态并复核本地产物哈希；上传仅报告远端同名可见")
    p.add_argument("--key", required=True)
    p.add_argument("--ledger", help="默认 <data_dir>/baidu_netdisk/ledger.json")

    p = sub.add_parser("inspect", help="只读诊断：枚举官方客户端主窗与原生对话框，截帧+OCR，不发输入")
    p.add_argument("--out", help="完整报告 JSON 写入路径（建议 %%TEMP%%/codeyun/...）")
    p.add_argument("--images", help="保留截帧 PNG 的目录")
    p.add_argument("--tokens", action="store_true", help="包含 OCR 二级 token")
    p.add_argument("--redact", action="store_true", help="非客户端界面标签的文字只保留长度")
    p.add_argument("--lease-token", help="复用已取得的 gui:desktop:local 租约 token")
    p.add_argument("--owner", default="baidu-netdisk-inspect")

    args = parser.parse_args(argv)
    if args.cmd == "inspect":
        out = run_inspect(args)
    elif args.cmd == "status":
        out = run_status(args)
    elif args.cmd == "resume":
        out = run_resume(args)
    elif args.cmd in {"upload", "download", "download-share"}:
        out = run_gui(args)
    elif args.cmd == "version":
        out = probe_client_version().to_dict()
    elif args.cmd == "snapshot":
        facts = snapshot_dir(args.root)
        write_manifest(args.out, args.root, facts)
        out = {"status": "ok", "root": args.root, "file_count": len(facts), "manifest": args.out}
    elif args.cmd == "collect":
        root, before = load_manifest(args.manifest)
        out = collect_download(download_root=root, before=before, expected_name=args.name,
                               expected_size=args.size, target_dir=args.target_dir,
                               expected_sha256=args.sha256, stable_token=args.stable_token).to_dict()
    elif args.cmd == "describe-source":
        out = describe_upload_source(args.path, args.remote_dir)
    elif args.cmd == "ledger-begin":
        record, created = TransferLedger(args.ledger).begin(args.op, **json.loads(args.inputs))
        out = {"created": created, "record": record}
    else:
        out = TransferLedger(args.ledger).update(args.key, args.state, **json.loads(args.facts))
    # ASCII-escaped: console code pages (GBK) cannot print every OCR character.
    print(json.dumps(out, ensure_ascii=True))
    return 0


@contextmanager
def _lease(args):
    from backend.core.runtime.resource_leases import DESKTOP_GUI_RESOURCE, ResourceLeaseStore, hold_resource
    if args.lease_token:
        store = ResourceLeaseStore()
        yield lambda: store.check(DESKTOP_GUI_RESOURCE, args.lease_token)
    else:
        with hold_resource(DESKTOP_GUI_RESOURCE, args.owner) as lease:
            yield lease.check


def _ledger(args) -> TransferLedger:
    from backend.core.settings import get_settings
    return TransferLedger(args.ledger or get_settings().data_dir / "baidu_netdisk" / "ledger.json")


def run_status(args) -> dict:
    from backend.core.baidu_netdisk.client import artifact_status
    record = _ledger(args).get(args.key)
    if record is None:
        return {"status": "unknown_key", "key": args.key}
    return artifact_status(record).to_dict()


def run_resume(args) -> dict:
    from backend.core.baidu_netdisk.client import NetdiskClient

    def no_gui():
        raise RuntimeError("resume 不允许 GUI 操作")
    client = NetdiskClient(ledger=_ledger(args), lease_check=no_gui, settle=args.interval, poll_attempts=args.attempts)
    return client.resume_download(args.key).to_dict()


def run_gui(args) -> dict:
    from backend.core.baidu_netdisk.client import NetdiskClient
    ledger = _ledger(args)
    with _lease(args) as check:
        check()
        client = NetdiskClient(ledger=ledger, lease_check=check)
        if args.cmd == "upload":
            result = client.upload_file(args.path, args.remote_dir)
        elif args.cmd == "download-share":
            pwd = os.environ.get(args.pwd_env) if args.pwd_env else None
            result = client.download_share(args.url, args.item, args.local_dir, client_download_dir=args.client_download_dir,
                                           expected_size=args.size, pwd=pwd, expected_sha256=args.sha256)
        else:
            result = client.download_file(args.remote_path, args.local_dir, client_download_dir=args.client_download_dir,
                                          expected_size=args.size, expected_sha256=args.sha256)
    return result.to_dict()


def run_inspect(args) -> dict:
    from backend.core.baidu_netdisk.client import ProjectionDriver, inspect_client
    images = Path(args.images) if args.images else None
    if images:
        images.mkdir(parents=True, exist_ok=True)
    with _lease(args) as check:
        report = inspect_client(ProjectionDriver(), check, redact=args.redact, with_tokens=args.tokens, image_dir=images)
    if not args.out:
        return report
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"out": args.out, "windows": [{k: w.get(k) for k in ("id", "title", "width", "height", "path", "error")}
                                         for w in report["windows"]]}


if __name__ == "__main__":
    raise SystemExit(main())
