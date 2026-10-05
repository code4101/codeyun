from io import BytesIO
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import wave

import pytest
from pyxllib.autogui.wechat_db import WeChatDbStorage, message_table_name
from backend.core.messaging import wechat_voice


@pytest.fixture
def storage(tmp_path):
    root=tmp_path/'db_storage'
    (root/'message').mkdir(parents=True)
    with sqlite3.connect(root/'message/message_0.db') as db:
        table=message_table_name('wxid_chat_a')
        db.execute(f'CREATE TABLE "{table}"(local_id INTEGER, local_type INTEGER, server_id INTEGER, message_content TEXT)')
        db.execute(f'INSERT INTO "{table}" VALUES(52,34,123,?)',('<msg><voicemsg voicelength="400"/></msg>',))
        db.execute(f'INSERT INTO "{table}" VALUES(53,1,124,"text")')
    with sqlite3.connect(root/'message/media_0.db') as db:
        db.execute('CREATE TABLE Name2Id(user_name TEXT)')
        db.executemany('INSERT INTO Name2Id VALUES(?)',[('wxid_chat_a',),('wxid_chat_b',)])
        db.execute('CREATE TABLE VoiceInfo(chat_name_id INTEGER,local_id INTEGER,svr_id INTEGER,data_index INTEGER,voice_data BLOB)')
        import pysilk
        encoded=BytesIO()
        pysilk.encode(BytesIO(b'\0\0'*6400),encoded,16000,16000)
        db.execute('INSERT INTO VoiceInfo VALUES(1,52,123,0,?)',(encoded.getvalue(),))
        db.execute('INSERT INTO VoiceInfo VALUES(2,52,123,0,?)',(b'foreign voice',))
        db.execute('INSERT INTO VoiceInfo VALUES(1,52,999,0,?)',(b'wrong server',))
    return WeChatDbStorage(root)


def test_exact_voice_resolution_exports_playable_audio_without_cross_chat_data(storage):
    voice=storage.voice_message('wxid_chat_a',52)
    assert voice['server_id']=='123' and voice['duration_seconds']==.4
    assert voice['text'] is None and voice['audio']['readable']
    with wave.open(voice['audio']['stored_path'],'rb') as wav:
        assert wav.getframerate()==16000 and wav.getnchannels()==1
        assert .3 < wav.getnframes()/wav.getframerate() < .5
    resources=storage.message_resources('wxid_chat_a',52)
    assert resources['items'][0]['kind']=='voice'
    assert resources['items'][0]['export']['stored_path']==voice['audio']['stored_path']


def test_non_voice_target_and_missing_chat_fail_closed(storage):
    with pytest.raises(ValueError,match='not a voice'):
        storage.voice_message('wxid_chat_a',53)
    with pytest.raises(ValueError,match='does not exist'):
        storage.voice_message('wxid_chat_b',52)


def test_transcription_cache_uses_audio_content_and_keeps_missing_audio_retryable(tmp_path,monkeypatch):
    from pyxllib.autogui import wechat_accounts
    wav=tmp_path/'input.wav'
    wav.write_bytes(b'first')
    voice={'server_id':'123','text':None,'audio':{'readable':True,'stored_path':str(wav)}}
    class Storage:
        def voice_message(self,*args): return voice
    monkeypatch.setattr(wechat_accounts,'get_account_storage',lambda _:Storage())
    monkeypatch.setattr(wechat_voice,'get_settings',lambda:SimpleNamespace(data_dir=tmp_path))
    root=tmp_path/'model'
    root.mkdir()
    for name in ('model.int8.onnx','tokens.txt'): (root/name).write_text('fixture')
    (root/'manifest.json').write_text(json.dumps({'files':{'model.int8.onnx':'hash'}}))
    monkeypatch.setattr(wechat_voice,'model_root',lambda:root)
    calls=[]
    monkeypatch.setattr(wechat_voice,'_recognize',lambda *args:calls.append(args) or '识别结果')
    assert wechat_voice.transcribe_voice('account','chat',52)['text'] is None
    assert not calls  # Official-only is the default, even with a local model installed.
    assert wechat_voice.transcribe_voice('account','chat',52,allow_local=True)['text']=='识别结果'
    assert wechat_voice.transcribe_voice('account','chat',52,allow_local=True)['cached']
    assert len(calls)==1
    wav.write_bytes(b'changed')
    assert not wechat_voice.transcribe_voice('account','chat',52,allow_local=True)['cached']
    assert len(calls)==2
    voice['audio']['readable']=False
    assert wechat_voice.transcribe_voice('account','chat',52).get('error')


def test_official_voice_metadata_validates_status_and_ignores_other_fields(storage):
    from pyxllib.autogui.wechat_voice import official_voice_text
    text='微信官方识别结果。'
    nested=b'\x08\x02\x12'+bytes([len(text.encode())])+text.encode()
    packed=b'\x08\x09\x10\x03\x2a'+bytes([len(nested)])+nested+b'\x58\x00'
    assert official_voice_text(packed)==text
    assert official_voice_text(packed[:-1]) is None
    assert official_voice_text(packed.replace(b'\x08\x02',b'\x08\x01')) is None
    assert official_voice_text(b'\x12'+bytes([len(text.encode())])+text.encode()) is None
    with sqlite3.connect(storage.root/'message/message_0.db') as db:
        table=message_table_name('wxid_chat_a')
        db.execute(f'ALTER TABLE "{table}" ADD COLUMN packed_info_data BLOB')
        db.execute(f'UPDATE "{table}" SET packed_info_data=? WHERE local_id=52',(packed,))
    voice=storage.voice_message('wxid_chat_a',52)
    assert voice['text']==text and voice['source']=='wechat-official'


def test_voice_enrichment_does_not_mutate_original_or_invent_failed_transcriptions(monkeypatch):
    messages=[{'seq':1,'local_id':52,'message_type':34,'text':'<msg/>'}, {'seq':2,'message_type':1,'text':'补充'}]
    monkeypatch.setattr(wechat_voice,'transcribe_voice',lambda *args:{'text':'语音内容','source':'local-sensevoice'})
    enriched=wechat_voice.voice_context(messages,'account','chat')
    assert '语音内容' in enriched[0]['text'] and 'local-sensevoice' in enriched[0]['text']
    assert messages[0]['text']=='<msg/>' and enriched[1] is messages[1]
    monkeypatch.setattr(wechat_voice,'transcribe_voice',lambda *args:(_ for _ in ()).throw(RuntimeError('下载待完成')))
    failed=wechat_voice.voice_context(messages,'account','chat')
    assert '不要猜测' in failed[0]['text'] and failed[0]['voice']['text'] is None
