"""Disposable localhost acceptance server. Never imports the production app.

uv run python integrations/project-graph/collaboration_server.py --output <temp-dir>
The manifest contains only short-lived test-account tokens for this new database.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise SystemExit('Test output must be inside the OS temporary directory')
    output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    db_path = output / f'collaboration-{uuid.uuid4().hex}.db'
    os.environ['CODEYUN_DATA_DIR'] = str(output)
    os.environ['CODEYUN_DATABASE_URL'] = f'sqlite:///{db_path.as_posix()}'
    os.environ['CODEYUN_ENV'] = 'test'
    os.environ['SECRET_KEY'] = uuid.uuid4().hex
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from sqlmodel import Session
    import uvicorn
    from backend.api.project_graph_files import router
    from backend.core.access.auth import create_user_access_token
    from backend.core.collaboration.objects import ObjectHead, ObjectValue, ObjectCommit
    from backend.db import engine
    from backend.models import User, GraphResource, ResourceAccessGrant, ResourceIdentity, AppSetting
    for model in (User, GraphResource, ResourceAccessGrant, ResourceIdentity, AppSetting,
                  ObjectHead, ObjectValue, ObjectCommit):
        model.__table__.create(engine)
    tokens = {}
    with Session(engine) as db:
        for identity in range(1, 33):
            user = User(id=identity, username=f'test{identity}', nickname=f'测试用户{identity}', hashed_password='')
            db.add(user)
        db.commit()
        for identity in range(1, 33):
            tokens[identity] = create_user_access_token(db.get(User, identity))
    app = FastAPI()
    class DropCommitAcknowledgment:
        """Transport fault injection, available only in this disposable fixture."""
        def __init__(self, application):
            self.application = application
        async def __call__(self, scope, receive, send):
            dropped = False
            async def faulty_send(message):
                nonlocal dropped
                if scope['type'] == 'websocket' and b'dropCommitAck=1' in scope.get('query_string', b''):
                    if message['type'] == 'websocket.send' and json.loads(message.get('text', '{}')).get('type') == 'commit':
                        dropped = True
                        return await send({'type': 'websocket.close', 'code': 1012})
                    if dropped and message['type'] == 'websocket.send':
                        return
                await send(message)
            await self.application(scope, receive, faulty_send)
    app.include_router(router, prefix='/api/project-graph/files')
    @app.get('/')
    def host():
        return HTMLResponse((Path(__file__).parent / 'collaboration_host.html').read_text(encoding='utf-8'))
    @app.get('/fixture/sessions')
    def sessions():
        with Session(engine) as db:
            return {i: create_user_access_token(db.get(User, i)) for i in range(1, 33)}
    @app.get('/api/auth/user-options')
    def user_options(q: str = '', limit: int = 30):
        return {'users': [{'id': i, 'username': f'test{i}', 'nickname': f'测试用户{i}'}
                          for i in range(1, 33) if q in f'test{i} 测试用户{i}'][:limit]}
    app.mount('/plugins/project-graph', StaticFiles(directory=root / 'frontend/public/plugins/project-graph'))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    url = f'http://127.0.0.1:{listener.getsockname()[1]}'
    (output / 'server.json').write_text(json.dumps({'url': url, 'database': str(db_path)}), encoding='utf-8')
    print(f'Isolated PG acceptance server: {url}', flush=True)
    server = uvicorn.Server(uvicorn.Config(DropCommitAcknowledgment(app), log_level='warning'))
    server.run(sockets=[listener])


if __name__ == '__main__':
    main()
