"""Check or apply official source chapter deltas through CodeYun's public API."""
from pathlib import Path
import sys
import argparse
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'xlproject' / 'src'))
import xlproject.loadenv
import requests
from backend.core.access.auth import create_local_owner_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book-id', required=True)
    parser.add_argument('--username', default='code4101')
    parser.add_argument('--apply', action='store_true', help='Apply safe deltas and persist the baseline')
    parser.add_argument('--base-url', default='http://localhost:8000')
    args = parser.parse_args()
    session = create_local_owner_session(username=args.username)
    headers = {'Authorization': 'Bearer ' + session['access_token']}
    url = args.base_url.rstrip('/') + '/api/linux-do-books/' + requests.utils.quote(args.book_id, safe='')
    book = requests.get(url, headers=headers, timeout=30)
    book.raise_for_status()
    result = requests.post(url + '/source-sync', headers=headers, json={
        'mode': 'apply' if args.apply else 'preview', 'revision': book.json()['revision'],
    }, timeout=180)
    result.raise_for_status()
    print(json.dumps(result.json(), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
