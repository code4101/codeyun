import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlmodel import Session, select

from backend.core.notes.merge import NoteMergeRequest, merge_notes, merge_metadata
from backend.models import NoteNode, NoteEdge


@pytest.fixture
def session():
    engine = create_engine('sqlite://')
    NoteNode.__table__.create(engine)
    NoteEdge.__table__.create(engine)
    with Session(engine) as session:
        session.add_all([
            NoteNode(id='a', numeric_id=1, user_id=2, title='Walk', content='original A'),
            NoteNode(id='b', numeric_id=2, user_id=2, title='Lunch', content='![photo](/p.jpg)',
                     custom_fields=[['source', 'json', {'message_id': 'chat:39'}]]),
            NoteNode(id='c', numeric_id=3, user_id=3, title='Other account'),
            NoteEdge(id='e1', user_id=2, source_id='a', target_id='outside'),
            NoteEdge(id='e2', user_id=2, source_id='b', target_id='outside'),
            NoteEdge(id='e3', user_id=2, source_id='b', target_id='a'),
        ])
        session.commit()
        yield session


def request(**overrides):
    values = dict(target_id='1', versions={'1': 1, '2': 1}, title='Walk and lunch',
                  content='Combined ![photo](/p.jpg)', mutation_id='merge-1')
    return NoteMergeRequest(**(values | overrides))


def test_merge_keeps_originals_archives_sources_moves_edges_and_retries(session):
    target = merge_notes(session, 2, request())
    assert target.version == 2 and target.content == request().content
    source = session.get(NoteNode, 'b')
    assert source.deleted_at and source.content == '![photo](/p.jpg)'
    assert merge_metadata(source, '_merged_into')['target_id'] == '1'
    originals = merge_metadata(target, '_merge_history')[0]['originals']
    assert originals[0]['content'] == 'original A'
    assert originals[1]['custom_fields'][0][2]['message_id'] == 'chat:39'
    edges = session.exec(select(NoteEdge)).all()
    assert [(e.source_id, e.target_id) for e in edges] == [('1', 'outside')]
    assert merge_notes(session, 2, request()).version == 2
    with pytest.raises(HTTPException) as error:
        merge_notes(session, 2, request(content='different'))
    assert error.value.status_code == 409


def test_stale_version_leaves_every_note_untouched(session):
    with pytest.raises(HTTPException) as error:
        merge_notes(session, 2, request(versions={'1': 1, '2': 99}))
    assert error.value.status_code == 409
    assert session.get(NoteNode, 'a').version == 1
    assert not session.get(NoteNode, 'b').deleted_at


def test_other_account_and_duplicate_aliases_are_refused(session):
    with pytest.raises(HTTPException) as error:
        merge_notes(session, 2, request(versions={'1': 1, '3': 1}))
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        merge_notes(session, 2, request(versions={'1': 1, 'a': 1}))
    assert error.value.status_code == 422


def test_old_link_redirects_to_accessible_canonical_note(session):
    from backend.api.notes import read_note
    from backend.models import User
    merge_notes(session, 2, request())
    reply = read_note('2', User(id=2, username='owner', hashed_password='x'), session)
    assert reply.status_code == 307 and reply.headers['location'] == '/api/notes/1'
    with pytest.raises(HTTPException) as error:
        read_note('2', User(id=3, username='other', hashed_password='x'), session)
    assert error.value.status_code == 404


def test_explicit_format_conversion_guards_versions_preserves_body_and_retries(session, monkeypatch):
    from backend.api import notes as api
    from backend.models import User
    monkeypatch.setattr(api, '_broadcast_note_resource_update', lambda *a, **k: None)
    original = session.get(NoteNode, 'a')
    original.format_type = 'markdown'
    session.add(original)
    session.commit()
    user = User(id=2, username='owner', hashed_password='x')
    req = api.NoteFormatConversionRequest(base_version=1, source_format='markdown',
        target_format='html', content='<p>12:00 Walk</p>', mutation_id='convert-1')
    converted = api.convert_note_format('1', req, user, session)
    assert converted['format_type'] == 'html' and converted['version'] == 2
    note = session.get(NoteNode, 'a')
    assert note.history[-1]['v']['content'] == 'original A'
    assert api.convert_note_format('1', req, user, session)['version'] == 2
    with pytest.raises(HTTPException) as error:
        api.convert_note_format('1', req.model_copy(update={'mutation_id': 'stale'}), user, session)
    assert error.value.status_code == 409
    assert session.get(NoteNode, 'a').content == '<p>12:00 Walk</p>'
