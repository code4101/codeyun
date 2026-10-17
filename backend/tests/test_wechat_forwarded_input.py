"""Merged-forward records reach the model as attributed, untrusted reference data."""
import html
import pytest
from backend.core.messaging.wechat_agent import model_message
from pyxllib.autogui.wechat_db import parse_message_appmsg


def forwarded_message(record):
    return {"seq": 1, "sender_id": "wxid_owner", "message_type": 49,
            "text": 'wxid_owner:\n<msg><appmsg><type>19</type><title>群聊的聊天记录</title>'
                    '<des>只有预览</des><recorditem><![CDATA[' + record + ']]></recorditem>'
                    '</appmsg></msg>'}


def test_full_forwarded_text_and_original_time_survive_preview_projection():
    message = forwarded_message('<recordinfo><datalist count="2">'
        '<dataitem datatype="1" dataid="one"><sourcename>代号4101</sourcename>'
        '<sourcetime>2026-10-06&#x20;10:55:58</sourcetime><srcMsgCreateTime>1791255358</srcMsgCreateTime>'
        '<fromnewmsgid>1761655923693451863</fromnewmsgid><datadesc>第一条完整内容</datadesc></dataitem>'
        '<dataitem datatype="1" dataid="two"><sourcename>其他人</sourcename>'
        '<datadesc>第二条，不在预览中</datadesc></dataitem></datalist></recordinfo>')
    result = model_message(message)
    assert result['des'] == '只有预览'
    assert [item['text'] for item in result['forwarded_items']] == ['第一条完整内容', '第二条，不在预览中']
    assert result['forwarded_items'][0]['source_timestamp'] == 1791255358
    assert result['forwarded_items'][0]['source_message_id'] == '1761655923693451863'
    assert result['forwarded_items'][0]['source_time'] == '2026-10-06 10:55:58'
    assert '不自动构成当前交办' in result['forwarded_instruction']
    assert result['sender_id'] == 'wxid_owner'


def test_forwarded_image_metadata_does_not_expose_cdn_credentials_or_claim_readable():
    message = forwarded_message('<recordinfo><datalist><dataitem datatype="2" dataid="photo">'
        '<datadesc>[图片]</datadesc><datasize>1234</datasize><cdndatakey>secret</cdndatakey>'
        '<cdndataurl>encrypted-url</cdndataurl></dataitem></datalist></recordinfo>')
    parsed = parse_message_appmsg(message['text'])
    assert parsed['forwarded_items'][0]['cdn_data_key'] == 'secret'
    item = model_message(message)['forwarded_items'][0]
    assert item['datatype'] == 2 and item['data_size'] == 1234
    assert 'cdn_data_key' not in item and 'cdn_data_url' not in item and 'export' not in item


def test_malformed_forwarded_record_does_not_turn_preview_into_complete_content():
    result = model_message(forwarded_message('<recordinfo><broken>'))
    assert result['forwarded_items'] == []
    assert '预览 des 不代表全文' in result['forwarded_instruction']


def test_forwarded_file_title_reaches_model_without_cdn_credentials():
    result = model_message(forwarded_message('<recordinfo><datalist><dataitem datatype="8">'
        '<datatitle>settings_副本.json</datatitle><datasize>1987</datasize>'
        '<cdndatakey>secret</cdndatakey></dataitem></datalist></recordinfo>'))
    item = result['forwarded_items'][0]
    assert item['file_name'] == 'settings_副本.json' and item['data_size'] == 1987
    assert 'cdn_data_key' not in item


@pytest.mark.parametrize('cdata', [True, False])
def test_record_xml_decodes_each_layer_once_with_url_queries_and_literal_entities(cdata):
    record = ('<recordinfo><datalist><dataitem datatype="1">'
              '<datadesc>A &amp; B; literal &amp;lt;tag&amp;gt;</datadesc>'
              '<sourceheadurl>https://example.invalid/?a=1&amp;b=2</sourceheadurl>'
              '</dataitem><dataitem datatype="8"><datatitle>settings.json</datatitle>'
              '</dataitem></datalist></recordinfo>')
    message = forwarded_message(record)
    if not cdata:
        message['text'] = message['text'].replace('<![CDATA[' + record + ']]>', html.escape(record))
    result = model_message(message)
    assert len(result['forwarded_items']) == 2
    assert result['forwarded_items'][0]['text'] == 'A & B; literal &lt;tag&gt;'
    assert result['forwarded_items'][1]['file_name'] == 'settings.json'
