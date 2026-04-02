import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlmodel import Session

from backend.db import engine
from backend.core.attendance_service import (
    decrypt_attendance_secret,
    get_current_account,
    get_or_create_attendance_service_config,
)
from backend.core.attendance_wjx import create_wjx_session, ensure_logged_in


TARGET_URL = "https://www.wjx.cn/wjx/design/designstart.aspx?activity=264266843&fromedit=1"
STATUS_PATH = ROOT / ".tmp-dev-observe" / "wjx_resume_debug_status.json"


def dump_status(tab, label: str) -> None:
    raw = tab.run_js(
        r"""
return JSON.stringify((() => {
  const runBtn = document.querySelector('#ctl02_ContentPlaceHolder1_btnRun')
    || Array.from(document.querySelectorAll('input,button,a')).find(el => (el.innerText || el.textContent || el.value || '').trim() === '恢复运行');
  const confirmModal = Array.from(document.querySelectorAll('div,section')).find(el => {
    const txt = (el.innerText || el.textContent || '').trim();
    return txt.includes('确认恢复运行吗');
  });
  const confirmBtn = confirmModal
    ? Array.from(confirmModal.querySelectorAll('input,button,a,span,div')).find(el => (el.innerText || el.textContent || el.value || '').trim() === '确定')
    : null;
  const cancelBtn = confirmModal
    ? Array.from(confirmModal.querySelectorAll('input,button,a,span,div')).find(el => (el.innerText || el.textContent || el.value || '').trim() === '取消')
    : null;
  return {
    url: location.href,
    title: document.title,
    runText: runBtn ? (runBtn.innerText || runBtn.textContent || runBtn.value || '').trim() : null,
    modalText: confirmModal ? (confirmModal.innerText || confirmModal.textContent || '').trim() : null,
    confirmText: confirmBtn ? (confirmBtn.innerText || confirmBtn.textContent || confirmBtn.value || '').trim() : null,
    cancelText: cancelBtn ? (cancelBtn.innerText || cancelBtn.textContent || cancelBtn.value || '').trim() : null,
  };
})())
"""
    )
    data = json.loads(raw) if isinstance(raw, str) else raw
    payload = {"label": label, **data}
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def activate(tab) -> None:
    try:
        tab.set.window.show()
    except Exception:
        pass
    try:
        tab.set.window.normal()
    except Exception:
        pass
    try:
        tab.set.window.max()
    except Exception:
        pass
    try:
        tab.browser.activate_tab(tab.tab_id)
    except Exception:
        pass


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "open"
    hold_seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 180

    with Session(engine) as db:
        config = get_or_create_attendance_service_config(db)
        account = get_current_account(db, config)
        username = account.login_username
        password = decrypt_attendance_secret(account.password_encrypted)

    session = create_wjx_session()
    try:
        ensure_logged_in(session, username=username, password=password, target_url=TARGET_URL)
        tab = session.tab
        activate(tab)
        dump_status(tab, "opened")

        if stage in {"click_restore", "confirm_restore"}:
            tab.run_js(
                r"""
(() => {
  const runBtn = document.querySelector('#ctl02_ContentPlaceHolder1_btnRun')
    || Array.from(document.querySelectorAll('input,button,a')).find(el => (el.innerText || el.textContent || el.value || '').trim() === '恢复运行');
  if (runBtn) runBtn.click();
})()
"""
            )
            time.sleep(1.5)
            activate(tab)
            dump_status(tab, "after_click_restore")

        if stage == "confirm_restore":
            tab.run_js(
                r"""
(() => {
  const confirmModal = Array.from(document.querySelectorAll('div,section')).find(el => {
    const txt = (el.innerText || el.textContent || '').trim();
    return txt.includes('确认恢复运行吗');
  });
  const confirmBtn = confirmModal
    ? Array.from(confirmModal.querySelectorAll('input,button,a,span,div')).find(el => (el.innerText || el.textContent || el.value || '').trim() === '确定')
    : null;
  if (confirmBtn) confirmBtn.click();
})()
"""
            )
            time.sleep(2)
            activate(tab)
            dump_status(tab, "after_confirm_restore")

        time.sleep(hold_seconds)
    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
