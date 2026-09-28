"""Repeat real-network protocol workloads; reports mean ± sample standard deviation.

This measures server/transport latency, not browser rendering. Run against the
disposable collaboration_server.py fixture, never the production instance.
"""
import argparse
import asyncio
import base64
import io
import math
import json
from pathlib import Path
import statistics
import time
import uuid
import zipfile

import httpx
import msgpack
import websockets


class Client:
    def __init__(self, socket):
        self.socket = socket
        self.pending = {}
        self.objects = {}
        self.revision = 0
        self.reader = None

    async def read(self):
        async for raw in self.socket:
            message = json.loads(raw)
            if message['type'] == 'commit' and message['revision'] > self.revision:
                assert message['revision'] == self.revision + 1
                self.revision = message['revision']
                for change in message['changes']:
                    if change['after'] is None:
                        self.objects.pop(change['id'], None)
                    else:
                        self.objects[change['id']] = change['after']
            future = self.pending.pop(message.get('id'), None)
            if future:
                if message['type'] == 'error':
                    future.set_exception(RuntimeError(str(message)))
                else:
                    future.set_result(message)

    async def request(self, **payload):
        identity = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        await self.socket.send(json.dumps({'id': identity, **payload}))
        return await asyncio.wait_for(future, 30)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--objects', type=int, default=500)
    parser.add_argument('--clients', type=int, nargs='+', default=[1, 4, 12])
    args = parser.parse_args()
    url = json.loads(args.manifest.read_text())['url']
    assert url.startswith('http://127.0.0.1:'), 'Only the disposable localhost fixture is allowed'
    async with httpx.AsyncClient(base_url=url, timeout=60) as http:
        tokens = (await http.get('/fixture/sessions')).json()
        async def api(path='', method='GET', body=None):
            response = await http.request(method, '/api/project-graph/files' + path,
                headers={'Authorization': 'Bearer ' + tokens['1']}, json=body)
            response.raise_for_status()
            return response.json()
        stage = [{'_': 'TextNode', 'uuid': str(uuid.uuid4()), 'text': f'Node {i}',
                  'details': [{'type': 'p', 'children': [{'text': 'x' * 256}]}]} for i in range(args.objects)]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('stage.msgpack', msgpack.packb(stage, use_bin_type=True))
        reports = []
        for count in args.clients:
            rounds = []
            for repeat in range(args.rounds):
                file = await api(method='POST', body={'title': f'benchmark-{count}-{repeat}-{uuid.uuid4().hex[:6]}',
                    'content': base64.b64encode(buffer.getvalue()).decode()})
                rid = file['id']
                for user in range(2, count + 1):
                    await api(f'/{rid}/access', 'PUT', {'userId': user, 'role': 'editor'})
                await api(f'/{rid}/collaboration', 'POST', {'expectedRevision': file['revision']})
                clients = []
                for user in range(1, count + 1):
                    socket = await websockets.connect(url.replace('http:', 'ws:') + f'/api/project-graph/files/{rid}/collaboration/socket', max_size=20_000_000)
                    await socket.send(json.dumps({'type': 'auth', 'token': tokens[str(user)]}))
                    joined = json.loads(await socket.recv())
                    assert joined['type'] == 'joined', joined
                    client = Client(socket)
                    client.objects = joined['objects']
                    client.reader = asyncio.create_task(client.read())
                    clients.append(client)
                latency = []
                start = time.perf_counter()
                async def edit(index, client):
                    identity = stage[index]['uuid']
                    lease = (await client.request(type='acquire', objects=[identity]))['tokens']
                    for sequence in range(5):
                        before = client.objects[identity]
                        after = {**before, 'text': f'{index}:{sequence}'}
                        began = time.perf_counter()
                        await client.request(type='commit', mutationId=str(uuid.uuid4()), tokens=lease,
                            changes=[dict(id=identity, before=before, after=after)])
                        latency.append((time.perf_counter() - began) * 1000)
                try:
                    await asyncio.gather(*(edit(index, client) for index, client in enumerate(clients)))
                    duration = time.perf_counter() - start
                    deadline = time.perf_counter() + 10
                    while any(client.revision != count * 5 for client in clients):
                        assert time.perf_counter() < deadline, 'Clients did not converge'
                        await asyncio.sleep(.01)
                    persisted = (await api(f'/{rid}/collaboration'))['objects']
                    assert all(client.objects == persisted for client in clients), 'Durable snapshot differs from clients'
                    rounds.append({'mean_ms': statistics.mean(latency), 'p95_ms': sorted(latency)[max(0, math.ceil(len(latency) * .95) - 1)],
                        'throughput_per_second': count * 5 / duration})
                finally:
                    for client in clients:
                        await client.socket.close()
                        await client.reader
            report = {'clients': count, 'objects': args.objects, 'rounds': rounds,
                'latency_mean_ms': statistics.mean(r['mean_ms'] for r in rounds),
                'latency_std_ms': statistics.stdev(r['mean_ms'] for r in rounds) if len(rounds) > 1 else 0,
                'throughput_mean': statistics.mean(r['throughput_per_second'] for r in rounds),
                'throughput_std': statistics.stdev(r['throughput_per_second'] for r in rounds) if len(rounds) > 1 else 0}
            reports.append(report)
            print(f"{count} clients, {args.objects} objects: {report['latency_mean_ms']:.1f} ± {report['latency_std_ms']:.1f} ms; "
                  f"{report['throughput_mean']:.1f} ± {report['throughput_std']:.1f} commits/s", flush=True)
        args.output.write_text(json.dumps(reports, indent=2), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(main())
