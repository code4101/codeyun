"""Voice input for the native-account bridge, independent of UI delivery.

The archive provider owns exact account/chat/message resolution and SILK
decoding. Local SenseVoice is an explicit fallback, not WeChat's official
transcriber. Cache only successful results by audio content and model version;
missing/download-pending audio stays retryable. Never upload recordings.
"""
from __future__ import annotations
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import threading
import wave

from filelock import FileLock
from backend.core.settings import get_settings

_INFERENCE_LOCK = threading.Lock()


def model_root() -> Path:
    return get_settings().data_dir / 'models' / 'wechat-sensevoice-int8-20240717'


@lru_cache(maxsize=1)
def _recognizer(root: str):
    import sherpa_onnx
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(Path(root)/'model.int8.onnx'), tokens=str(Path(root)/'tokens.txt'),
        num_threads=2, language='auto', use_itn=True, provider='cpu')


def _recognize(path: Path, root: Path) -> str:
    import numpy as np
    with wave.open(str(path),'rb') as wav:
        if wav.getnchannels()!=1 or wav.getsampwidth()!=2 or wav.getframerate()!=16000:
            raise ValueError('Voice audio must be 16kHz mono PCM16')
        if not 0 < wav.getnframes() <= 16000*300:
            raise ValueError('Voice audio exceeds the five-minute limit')
        samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').astype(np.float32)/32768
    with _INFERENCE_LOCK:
        recognizer=_recognizer(str(root))
        stream=recognizer.create_stream()
        stream.accept_waveform(16000,samples)
        recognizer.decode_stream(stream)
        return stream.result.text.strip()


def transcribe_voice(account_id: str, chat_id: str, local_id: int, *, allow_local: bool = False) -> dict:
    """Resolve one exact voice and return provenance-bearing text or an error.

    Official WeChat text is authoritative; local recognition requires an
    explicit allow_local=True. No GUI action is implicit. The worker calls this before
    model submission; archive polling remains independent. A recording with no
    recognized speech is reported as unreadable, never an invented user request.
    """
    from pyxllib.autogui.wechat_accounts import get_account_storage
    voice=get_account_storage(account_id).voice_message(chat_id,local_id)
    if voice.get('text'):
        return voice
    if not allow_local:
        return {**voice, 'error': '微信官方转文字尚未完成；需在微信中触发转文字后重新同步。默认不使用其他识别模型'}
    audio=voice.get('audio') or {}
    if not audio.get('readable') or not audio.get('stored_path'):
        return {**voice,'error':audio.get('read_error') or voice.get('error') or '语音尚未下载或解码'}
    root=model_root()
    if not all((root/name).is_file() for name in ('model.int8.onnx','tokens.txt','manifest.json')):
        return {**voice,'error':'本地语音模型尚未安装；运行 scripts/setup_wechat_voice_model.py'}
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    identity=hashlib.sha256((account_id+'\0'+chat_id+'\0'+str(voice['server_id'])).encode()).hexdigest()
    wav=Path(audio['stored_path'])
    fingerprint=hashlib.sha256(wav.read_bytes()).hexdigest()
    cache_dir=get_settings().data_dir/'wechat-agent'/'voice-transcripts'
    cache_dir.mkdir(parents=True,exist_ok=True)
    cache=cache_dir/(identity+'.json')
    model_hash=manifest['files']['model.int8.onnx']
    with FileLock(str(cache.with_suffix('.lock')),timeout=60):
        if cache.is_file():
            saved=json.loads(cache.read_text(encoding='utf-8'))
            if saved.get('audio_sha256')==fingerprint and saved.get('model_sha256')==model_hash:
                return {**voice,**saved,'cached':True}
        text=_recognize(wav,root)
        if not text:
            return {**voice,'error':'本地识别未检出可识别的语音内容'}
        saved={'text':text,'source':'local-sensevoice','audio_sha256':fingerprint,'model_sha256':model_hash}
        temp=cache.with_suffix('.tmp')
        temp.write_text(json.dumps(saved,ensure_ascii=False),encoding='utf-8')
        temp.replace(cache)
        return {**voice,**saved,'cached':False}


def voice_context(messages: list[dict], account_id: str, chat_id: str) -> list[dict]:
    """Enrich business input while keeping original inbox events immutable."""
    result=[]
    for message in messages:
        if message.get('message_type') not in (34,'34'):
            result.append(message)
            continue
        try:
            voice=transcribe_voice(account_id,chat_id,int(message['local_id']))
        except Exception as exc:
            voice={'text':None,'error':str(exc)}
        text=voice.get('text')
        body=(f"[语音转写，来源={voice.get('source')}，可能存在识别错误]\n{text}" if text else
              f"[语音暂未识别：{voice.get('error','未知错误')}；先在对应微信账号和聊天中使用官方转文字，"
              f"随后调用 get_account_storage(account_id).sync_from_live(export_media=False) 并通过 "
              f"voice_message(chat_id, local_id) 读取官方结果；不要猜测语音内容]")
        result.append({**message,'text':body,'voice':voice})
    return result
