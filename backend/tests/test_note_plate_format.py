import json
import pytest
from fastapi import HTTPException
from sqlmodel import Session, create_engine
from backend.api.notes import create_note, update_note
from backend.core.notes.document_formats import validate_note_body
from backend.core.notes.access import note_to_response_dict
from backend.models import User, AppSetting, ResourceIdentity, NoteNode, NoteEdge
from backend.schemas import NoteCreate, NoteUpdate, NoteRead


def body(text):
    return json.dumps({'schema': 'codeyun.plate', 'version': 1, 'value': [{'type': 'p', 'children': [{'text': text}]}]}, ensure_ascii=False)


def test_plate_roundtrip_and_field_conflicts():
    engine = create_engine('sqlite:///:memory:')
    for model in [User, AppSetting, ResourceIdentity, NoteNode, NoteEdge]:
        model.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        user = User(username='plate-test', hashed_password='x', is_superuser=True)
        session.add(user); session.commit(); session.refresh(user)
        created = create_note(NoteCreate(title='Plate', format_type='plate', content=body('初始')), user, session)
        assert NoteRead.model_validate(created).format_type == 'plate'
        note_id = str(created['id'])
        updated = update_note(note_id, NoteUpdate(content=body('修改'), base_version=created['version'], expected_fields={'content': body('初始')}), user, session)
        assert updated['content'] == body('修改')
        # Metadata changes do not conflict with another tab's independent content edit.
        renamed = update_note(note_id, NoteUpdate(title='改名', base_version=created['version'], expected_fields={'title': 'Plate'}), user, session)
        assert renamed['content'] == body('修改')
        with pytest.raises(HTTPException) as conflict:
            update_note(note_id, NoteUpdate(content=body('冲突'), expected_fields={'content': body('初始')}), user, session)
        assert conflict.value.status_code == 409
        with pytest.raises(HTTPException) as invalid:
            update_note(note_id, NoteUpdate(content='<p>不能当作 HTML 写入</p>'), user, session)
        assert invalid.value.status_code == 422
        with pytest.raises(HTTPException):
            update_note(note_id, NoteUpdate(format_type='html'), user, session)
        assert session.get(NoteNode, note_id).content == body('修改')
        html = create_note(NoteCreate(title='HTML', content='<p><strong>保留</strong></p>'), user, session)
        assert html['format_type'] == 'html'
        assert html['content'] == '<p><strong>保留</strong></p>'


@pytest.mark.parametrize('content', ['{}', '[]', '<p>正文</p>', '{"schema":"codeyun.plate","version":2,"value":[]}', '{"schema":"codeyun.plate","version":1,"value":[{"type":"p","children":[]}]}'])
def test_reject_invalid_plate(content):
    with pytest.raises(ValueError):
        validate_note_body('plate', content)


def test_plate_read_does_not_run_html_normalization():
    text = body('<script>文字示例</script>')
    note = NoteNode(id='1', user_id=1, format_type='plate', content=text)
    assert note_to_response_dict(note, None)['content'] == text


def test_recursive_collapse_preserves_metadata_and_rich_children():
    value = [{'type': 'codeyun-collapse', 'title': '外层', 'collapsed': True, 'children': [
        {'type': 'codeyun-collapse', 'title': '内层', 'collapsed': False, 'children': [
            {'type': 'p', 'children': [{'text': '正文', 'bold': True}]},
            {'type': 'img', 'url': 'data:image/png;base64,fixture', 'children': [{'text': ''}]},
        ]},
    ]}]
    content = json.dumps({'schema': 'codeyun.plate', 'version': 1, 'value': value}, ensure_ascii=False)
    validate_note_body('plate', content)
    note = NoteNode(id='1', user_id=1, format_type='plate', content=content)
    assert json.loads(note_to_response_dict(note, None)['content'])['value'] == value
