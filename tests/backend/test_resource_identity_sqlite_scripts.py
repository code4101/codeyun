import sqlite3

from scripts.resource_identity_sqlite import allocate_note_numeric_id, allocate_resource_id, ensure_device_file_resource, insert_note_edge


def test_sqlite_import_helper_allocates_identity_and_numeric_edges():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("create table sheetdocument(id text primary key, numeric_id integer)")
    con.execute("create table workbookdocument(id text primary key, numeric_id integer)")
    con.execute("create table pdfdocument(id text primary key, numeric_id integer)")
    con.execute("create table documentasset(id text primary key, numeric_id integer)")
    con.execute("create table notenode(id text primary key, numeric_id integer)")
    con.execute("create table noteedge(id text primary key, user_id integer, source_id text, target_id text, label text, created_at float)")
    con.execute("insert into sheetdocument(id,numeric_id) values ('sheet-a', 1)")
    con.execute("insert into workbookdocument(id,numeric_id) values ('workbook-a', 1)")

    parent_numeric_id = allocate_note_numeric_id(con, "parent-note")
    con.execute("insert into notenode(id,numeric_id) values (?,?)", ("parent-note", parent_numeric_id))
    child_numeric_id = allocate_note_numeric_id(con, "child-note")
    con.execute("insert into notenode(id,numeric_id) values (?,?)", ("child-note", child_numeric_id))

    assert parent_numeric_id == 2
    assert child_numeric_id == 3

    inserted = insert_note_edge(
        con,
        user_id=1,
        source_id="parent-note",
        target_id="child-note",
        edge_id="edge-a",
    )

    assert inserted is True
    edge = con.execute("select source_id,target_id from noteedge where id='edge-a'").fetchone()
    assert dict(edge) == {"source_id": "2", "target_id": "3"}
    identities = con.execute("select resource_type,legacy_pk,id from resourceidentity order by id").fetchall()
    assert [(row["resource_type"], row["legacy_pk"], row["id"]) for row in identities] == [
        ("note", "parent-note", 2),
        ("note", "child-note", 3),
    ]


def test_sqlite_import_helper_preserves_existing_resource_numeric_id():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("create table sheetdocument(id text primary key, numeric_id integer)")
    con.execute("create table workbookdocument(id text primary key, numeric_id integer)")
    con.execute("create table pdfdocument(id text primary key, numeric_id integer)")
    con.execute("create table documentasset(id text primary key, numeric_id integer)")
    con.execute("create table notenode(id text primary key, numeric_id integer)")
    con.execute("insert into sheetdocument(id,numeric_id) values ('sheet-a', 7)")

    sheet_id = allocate_resource_id(con, "sheet", "sheet-a", preferred_id=7)

    assert sheet_id == 7
    identity = con.execute("select resource_type,legacy_pk from resourceidentity where id=7").fetchone()
    assert dict(identity) == {"resource_type": "sheet", "legacy_pk": "sheet-a"}


def test_sqlite_import_helper_indexes_device_file_resources(tmp_path):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("create table sheetdocument(id text primary key, numeric_id integer)")
    con.execute("create table workbookdocument(id text primary key, numeric_id integer)")
    con.execute("create table pdfdocument(id text primary key, numeric_id integer)")
    con.execute("create table documentasset(id text primary key, numeric_id integer)")
    con.execute("create table notenode(id text primary key, numeric_id integer)")
    con.execute(
        """
        create table devicefile(
            id integer primary key autoincrement,
            numeric_id integer,
            device_id text,
            absolute_path text,
            last_known_path text,
            file_size integer,
            modified_at_ms integer,
            media_kind text,
            mime_type text,
            match_status text,
            created_at float,
            updated_at float,
            last_seen_at float
        )
        """
    )
    con.execute("insert into sheetdocument(id,numeric_id) values ('sheet-a', 1)")
    image_path = tmp_path / "a.png"
    image_path.write_bytes(b"png")

    resource_id = ensure_device_file_resource(con, image_path, device_id="test-device", mime_type="image/png")
    same_resource_id = ensure_device_file_resource(con, image_path, device_id="test-device", mime_type="image/png")

    assert resource_id == 2
    assert same_resource_id == resource_id
    row = con.execute("select numeric_id,media_kind,mime_type from devicefile").fetchone()
    assert dict(row) == {"numeric_id": 2, "media_kind": "image", "mime_type": "image/png"}
    identity = con.execute("select resource_type,legacy_pk from resourceidentity where id=2").fetchone()
    assert dict(identity) == {"resource_type": "device_file", "legacy_pk": "1"}
