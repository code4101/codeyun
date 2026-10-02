"""Owner private production loop: incoming text -> default Codex -> pasted image.

Sends only a synthetic fixture between the owner's two authorized accounts.
The sender archive must contain the fixture pixels and the receiver must contain
the identical server message ID. Receiver originals may await WeChat download.
"""
from pathlib import Path
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
    ensure_attendance_engine_importable()
    import xlproject.loadenv  # noqa: F401
    from PIL import Image, ImageDraw
    from backend.core.messaging.wechat_agent import ATTENDANCE_ACCOUNT, OWNER_ACCOUNT, wechat_agent_status
    from backend.core.messaging.wechat_media_delivery import same_image
    from backend.core.temp_paths import codeyun_temp_root
    from pyxllib.autogui.wechat_accounts import get_account_storage
    from pyxllib.autogui.weixin4_instrumentation import send_text

    for _ in range(60):
        try:
            state = wechat_agent_status()
            if state.get("running") and any(h["key"] == "owner-private" for h in state.get("hooks", [])):
                break
        except OSError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Production private hook not ready")
    resume = len(sys.argv) == 3 and sys.argv[1] == "--resume"
    token = sys.argv[2] if resume else str(int(time.time()))
    if not token.isdecimal():
        raise ValueError("Resume token must be numeric")
    root = codeyun_temp_root("wechat-media-production") / token
    root.mkdir(parents=True, exist_ok=True)
    path = root / "bridge.png"
    if not resume:
        picture = Image.new("RGB", (640, 360), "white")
        draw = ImageDraw.Draw(picture)
        draw.rectangle((20, 20, 620, 250), fill="#2676c8")
        draw.text((40, 280), f"CodeYun clipboard production {token}", fill="black")
        picture.save(path)
    storage = get_account_storage(OWNER_ACCOUNT)
    sender_storage = get_account_storage(ATTENDANCE_ACCOUNT)
    cursor = None
    request_path = root / "request.json"
    if resume:
        before = set(json.loads(request_path.read_text(encoding="utf-8"))["before"]) if request_path.exists() else set()
    else:
        cursor = storage.poll_updates()["cursor"]
        before = {str(m["server_id"]) for m in storage.list_messages(ATTENDANCE_ACCOUNT, limit=20, include_resources=False)["items"]}
        request_path.write_text(json.dumps({"before": sorted(before), "token": token}), encoding="utf-8")
        send_text(ATTENDANCE_ACCOUNT, f"图片发送全链路联调 {token}：请把本机图片 {path} 回传给我，正文只回复“图片联调 {token}”。只发送这张测试图片，不改业务数据。",
                  sender_account_id=OWNER_ACCOUNT)
    deadline = time.monotonic() + 900
    sender_cursor = None
    while time.monotonic() < deadline:
        try:
            cursor = storage.poll_updates(cursor)["cursor"]
            sender_cursor = sender_storage.poll_updates(sender_cursor)["cursor"]
        except Exception as exc:
            if "Source generation changed" not in str(exc):
                raise
            time.sleep(2)
            continue
        for message in storage.list_messages(ATTENDANCE_ACCOUNT, limit=20, include_resources=False)["items"]:
            if (str(message["server_id"]) in before or message.get("sender_username") != ATTENDANCE_ACCOUNT
                    or message.get("local_type_normalized") != 3):
                continue
            outgoing = next((m for m in sender_storage.list_messages(OWNER_ACCOUNT, limit=20, include_resources=False)["items"]
                             if str(m["server_id"]) == str(message["server_id"]) and m.get("sender_username") == ATTENDANCE_ACCOUNT), None)
            if not outgoing:
                continue
            for item in sender_storage.message_resources(OWNER_ACCOUNT, outgoing["local_id"])["items"]:
                export = item.get("export", {})
                if export.get("readable") and export.get("kind") == "image" and same_image(path, Path(export["stored_path"])):
                    evidence = {"token": token, "sender_account_id": ATTENDANCE_ACCOUNT, "recipient_id": OWNER_ACCOUNT,
                                "server_id": str(message["server_id"]), "image_path": str(path), "verified": True,
                                "sender_pixels_verified": True, "receiver_server_id_verified": True}
                    (root / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(json.dumps(evidence, ensure_ascii=False))
                    return
        time.sleep(2)
    raise TimeoutError("Production image not confirmed; inspect outbox, never blindly resend")


if __name__ == "__main__":
    main()
