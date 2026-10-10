"""Prompt for secrets with hidden input and write them into the repo .env.

Values are never echoed or printed; only the key names are reported.
Example: uv run python scripts/set_env_secret.py HAHAMODEL_USERNAME HAHAMODEL_PASSWORD
"""
from pathlib import Path
import argparse
import getpass
import re
import sys

ENV_FILE = Path(__file__).resolve().parents[1] / '.env'
KEY_PATTERN = re.compile(r'^[A-Z][A-Z0-9_]*$')


def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def upsert(text, key, value):
    line = f'{key}={quote(value)}'
    pattern = re.compile(rf'^{re.escape(key)}=.*$', re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(lambda _: line, text, count=1)
    if text and not text.endswith('\n'):
        text += '\n'
    return text + line + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('keys', nargs='+', help='Upper-case env names, e.g. HAHAMODEL_PASSWORD')
    args = parser.parse_args()
    for key in args.keys:
        if not KEY_PATTERN.match(key):
            parser.error(f'invalid key: {key}')
    if not sys.stdin.isatty():
        parser.error('needs an interactive terminal so the input stays hidden')
    values = {}
    for key in args.keys:
        while True:
            value = getpass.getpass(f'{key}: ')
            if not value:
                parser.error(f'{key} is empty; nothing written')
            if not value.isascii():
                # A Chinese IME silently turns punctuation into full-width characters.
                print('输入含全角/非 ASCII 字符，可能是输入法未切到英文，请重新输入')
                continue
            if getpass.getpass(f'{key} (again): ') != value:
                print('两次输入不一致，请重新输入')
                continue
            break
        values[key] = value
    text = ENV_FILE.read_text(encoding='utf-8') if ENV_FILE.exists() else ''
    for key, value in values.items():
        text = upsert(text, key, value)
    ENV_FILE.write_text(text, encoding='utf-8')
    print(f'wrote {", ".join(values)} to {ENV_FILE}')


if __name__ == '__main__':
    main()
