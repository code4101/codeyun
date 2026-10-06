from backend.core.library.source_sync import ORIGIN, plan_sync, quote_offset, semantic_html

URL = ORIGIN + '/ch01'


def source(body='<p>旧文</p>'):
    return ('<html><head><title>书</title></head><body>'
            '<h1 data-toc-anchor="article-1" data-toc-level="1">第一章</h1>'
            f'<p>来源：<a href="{URL}">原文</a></p>{body}'
            '<h1 data-toc-anchor="article-2" data-toc-level="1">本地章节</h1><p>不动</p></body></html>')


def test_baseline_then_incremental_update_keeps_ids_and_local_chapter():
    original = source()
    first = plan_sync(original, {URL: '旧文'}, {})
    assert first['content'] == original
    assert first['unchanged'] == ['article-1']
    next_plan = plan_sync(original, {URL: '新文'}, first['baseline'])
    assert next_plan['updated'] == ['article-1']
    assert '<p>新文</p>' in next_plan['content']
    assert '<h1 data-toc-anchor="article-2" data-toc-level="1">本地章节</h1><p>不动</p>' in next_plan['content']
    repeat = plan_sync(next_plan['content'], {URL: '新文'}, next_plan['baseline'])
    assert repeat['content'] == next_plan['content']
    assert not repeat['updated']


def test_local_edits_and_unbased_differences_are_never_overwritten():
    first = plan_sync(source(), {URL: '旧文'}, {})
    for baseline in ({}, first['baseline']):
        result = plan_sync(source('<p>人工修改</p>'), {URL: '新版'}, baseline)
        assert result['content'] == source('<p>人工修改</p>')
        assert result['conflicts']
    result = plan_sync(source('<p>人工修改</p>'), {URL: '旧文'}, first['baseline'])
    assert not result['updated']
    assert '人工修改' in result['content']


def test_highlight_loss_blocks_chapter_but_safe_quote_allows_update():
    first = plan_sync(source(), {URL: '旧文'}, {})
    annotation = {'chapter_id': 'article-1', 'quote_text': '旧文', 'prefix_text': '', 'suffix_text': ''}
    result = plan_sync(source(), {URL: '全新内容'}, first['baseline'], annotations=[annotation])
    assert result['conflicts'] and result['content'] == source()
    result = plan_sync(source(), {URL: '旧文 新增内容'}, first['baseline'], annotations=[annotation])
    assert result['updated'] == ['article-1']
    result = plan_sync(source(), {URL: '旧文 旧文'}, first['baseline'], annotations=[annotation])
    assert result['conflicts']


def test_addition_uses_stable_id_and_removed_page_stays_local():
    added_url = ORIGIN + '/ch24'
    result = plan_sync(source(), {added_url: '新增'}, {}, catalog={added_url: '第24章'})
    assert result['added'][0].startswith('source-')
    assert 'article-1' in result['content'] and '旧文' in result['content']
    assert result['content'].endswith('</body></html>')
    repeat = plan_sync(result['content'], {added_url: '新增'}, result['baseline'], catalog={added_url: '第24章'})
    assert not repeat['added']
    assert result['conflicts']


def test_semantic_hash_preserves_code_indentation_and_quote_is_unique():
    assert semantic_html('<p>a b</p>') == semantic_html('<p>a\nb</p>')
    assert semantic_html('<pre>  x</pre>') != semantic_html('<pre>x</pre>')
    assert quote_offset('left Q right Q', 'Q') == -1
    assert quote_offset('left Q right Q', 'Q', 'left ', ' right') == 5
    assert quote_offset('updated Q end', 'Q', 'old ', ' context') == 8


def test_sanitize_upstream_html_and_keep_markdown_code():
    first = plan_sync(source(), {URL: '旧文'}, {})
    result = plan_sync(source(), {URL: '<script>x</script>\n\n```\n  x\n```'}, first['baseline'])
    assert '<script>' not in result['content']
    assert '  x\n' in result['content']


def test_source_sync_api_preserves_reader_data_and_assets(monkeypatch, tmp_path):
    import pytest
    from fastapi import HTTPException
    from sqlmodel import Session, SQLModel, create_engine, select
    from sqlalchemy.pool import StaticPool
    from backend.api import linux_do_books as api
    from backend.core.library.ebook_import import import_ebook
    from backend.core.library.linux_do_book import LinuxDoBookDocument
    from backend.models import (User, LibraryBookAsset, LibraryBookPlacement, PdfLibraryBookshelf,
                                LibraryAnnotation, LibraryReadingNote, LibraryReadingState)
    from backend.core.library import source_sync
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    storage = tmp_path / 'book'
    storage.mkdir()
    path = storage / 'source.html'
    path.write_text(source(), encoding='utf8')
    (storage / 'resources').mkdir()
    (storage / 'resources' / 'cover.jpg').write_bytes(b'cover-preserved')
    monkeypatch.setattr(api, '_storage_path', lambda *args: storage / 'document.json')
    upstream = {URL: '旧文'}
    monkeypatch.setattr(source_sync, 'fetch_sources', lambda content: (dict(upstream), {URL: '第一章'}, {}))
    with Session(engine) as session:
        owner = User(username='sync-owner', hashed_password='test')
        session.add(owner); session.commit(); session.refresh(owner)
        shelf = PdfLibraryBookshelf(user_id=owner.id, name='书架')
        session.add(shelf); session.commit(); session.refresh(shelf)
        asset = LibraryBookAsset(id='ebook:sync-test', resource_type=api.BOOK_RESOURCE_TYPE,
                                 owner_user_id=owner.id, source_kind='ebook:test', title='书',
                                 metadata_json={'topic_id': -1, 'book_kind': 'ebook', 'format': 'html', 'original_filename': 'source.html'})
        session.add(asset)
        session.add(LibraryBookPlacement(book_asset_id=asset.id, user_id=owner.id, bookshelf_id=shelf.id))
        annotation = LibraryAnnotation(user_id=owner.id, resource_type='rich-text', resource_id=asset.id,
                                       chapter_id='article-1', quote_text='旧文', comment_text='我的批注')
        note = LibraryReadingNote(id='note-sync', user_id=owner.id, resource_type='rich-text', resource_id=asset.id,
                                  chapter_id='article-1', content='我的笔记', revision=1)
        state = LibraryReadingState(user_id=owner.id, resource_type=api.BOOK_RESOURCE_TYPE,
                                    resource_id=asset.id, chapter_id='article-1')
        for record in (annotation, note, state):
            session.add(record)
        session.commit()
        imported = import_ebook(path, book_id=asset.id, filename='source.html')
        document = LinuxDoBookDocument(topic_id=-1, title='书', author='', source_url='',
                                      content_html=imported.content_html, content_markdown=imported.content_text,
                                      toc=imported.toc, revision=imported.revision, post_count=2,
                                      selected_reply_count=0, imported_at=1)
        api._write_document(owner.id, document)
        for record in (annotation, note, state):
            session.refresh(record)
        before = [record.model_dump() for record in (annotation, note, state)]
        preview = api.sync_ebook_source(asset.id, api.EbookSourceSyncRequest(revision=document.revision), session, owner)
        assert preview['baseline_count'] == 1 and 'source_sync_baseline' not in asset.metadata_json
        baseline = api.sync_ebook_source(asset.id, api.EbookSourceSyncRequest(mode='apply', revision=document.revision), session, owner)
        assert baseline['revision'] == document.revision
        upstream[URL] = '旧文 新增'
        updated = api.sync_ebook_source(asset.id, api.EbookSourceSyncRequest(mode='apply', revision=document.revision), session, owner)
        assert updated['updated'] == ['article-1']
        assert [item.anchor for item in api._read_document(asset).toc] == ['article-1', 'article-2']
        for record in (annotation, note, state):
            session.refresh(record)
        assert before == [record.model_dump() for record in (annotation, note, state)]
        assert (storage / 'resources' / 'cover.jpg').read_bytes() == b'cover-preserved'
        upstream[URL] = '已删除高亮句子'
        blocked = api.sync_ebook_source(asset.id, api.EbookSourceSyncRequest(mode='apply', revision=updated['revision']), session, owner)
        assert blocked['conflicts'] and blocked['revision'] == updated['revision']
        with pytest.raises(HTTPException) as exc:
            api.sync_ebook_source(asset.id, api.EbookSourceSyncRequest(mode='apply', revision='stale'), session, owner)
        assert exc.value.status_code == 409
