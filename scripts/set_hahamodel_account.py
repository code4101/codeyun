"""Enter the hahamodel.com account in plain text, verify it by logging in, then save to .env.

Input is visible on purpose so typos are easy to spot; nothing is written until
the site accepts the login. Run it in your own terminal:
    uv run python scripts/set_hahamodel_account.py
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))


def main():
    import requests
    from backend.core.hahamodel_usage import (
        HAHAMODEL_PASSWORD_ENV,
        HAHAMODEL_USERNAME_ENV,
        HahaModelError,
        login_hahamodel,
        read_hahamodel_credentials,
    )
    from set_env_secret import ENV_FILE, upsert

    username = read_hahamodel_credentials()[0] or 'code4101'
    print(f'hahamodel 用户名：{username}（取自 .env）')
    while True:
        password = input('hahamodel 密码（明文显示）: ').strip()
        if not password:
            print('密码为空，请重新输入')
            continue
        if not password.isascii():
            print('密码含全角/非 ASCII 字符，可能是输入法未切到英文，请重新输入')
            continue
        if input('再输入一次确认: ').strip() != password:
            print('两次输入不一致，请重新输入')
            continue
        try:
            with requests.Session() as http:
                login_hahamodel(http, username, password)
        except (requests.RequestException, HahaModelError) as exc:
            print(f'校验失败：{exc}\n请核对后重新输入（Ctrl+C 退出）')
            continue
        break
    text = ENV_FILE.read_text(encoding='utf-8') if ENV_FILE.exists() else ''
    text = upsert(text, HAHAMODEL_USERNAME_ENV, username)
    text = upsert(text, HAHAMODEL_PASSWORD_ENV, password)
    ENV_FILE.write_text(text, encoding='utf-8')
    print(f'登录校验通过，已写入 {ENV_FILE}；到页面点「刷新」即可')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\n已取消，未写入')
