"""Install the documented local SenseVoice INT8 assets once, outside source."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

import xlproject.loadenv
import requests
from filelock import FileLock
from backend.core.settings import get_settings
from backend.core.temp_paths import codeyun_temp_root

URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2'


def main():
    target = get_settings().data_dir / 'models' / 'wechat-sensevoice-int8-20240717'
    target.mkdir(parents=True, exist_ok=True)
    with FileLock(str(target / 'install.lock'), timeout=10):
        if all((target / name).is_file() for name in ('model.int8.onnx','tokens.txt','manifest.json')):
            print(json.dumps({'ready':True,'path':str(target)},ensure_ascii=True))
            return
        if shutil.disk_usage(target).free < 1024**3:
            raise RuntimeError('At least 1 GiB free space is required')
        archive = codeyun_temp_root('wechat-voice') / 'sensevoice-int8-download.tar.bz2'
        digest = hashlib.sha256()
        try:
            with requests.get(URL,stream=True,timeout=(15,45)) as response:
                response.raise_for_status()
                with archive.open('wb') as out:
                    for chunk in response.iter_content(1024*1024):
                        out.write(chunk)
                        digest.update(chunk)
                        if out.tell() > 512*1024**2:
                            raise RuntimeError('Model archive exceeds expected size')
            names={'model.int8.onnx','tokens.txt','LICENSE','README.md'}
            with tarfile.open(archive,'r:bz2') as tar:
                found=set()
                for member in tar:
                    name=Path(member.name).name
                    if name in names and member.isfile():
                        if name in found:
                            raise RuntimeError('Duplicate model asset')
                        with tar.extractfile(member) as src, (target/(name+'.part')).open('wb') as out:
                            shutil.copyfileobj(src,out)
                        (target/(name+'.part')).replace(target/name)
                        found.add(name)
                if not {'model.int8.onnx','tokens.txt'} <= found:
                    raise RuntimeError('Missing model assets')
            manifest={'url':URL,'archive_sha256':digest.hexdigest(),
                      'files':{name:hashlib.sha256((target/name).read_bytes()).hexdigest() for name in found}}
            (target/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
            print(json.dumps({'ready':True,'path':str(target),'files':found},default=list,ensure_ascii=True))
        finally:
            archive.unlink(missing_ok=True)


if __name__=='__main__': main()
