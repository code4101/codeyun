from copy import deepcopy
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from backend.core.attendance import workbook_registry
from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
from backend.models import WorkbookDocument, SheetDocument, ResourceAccessGrant, ResourceIdentity

ensure_attendance_engine_importable()
from xlsln.kq5034.engine import client, monthly_courses


def test_monthly_registry_recovers_conflicts_inherits_access_and_retries(monkeypatch):
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(workbook_registry,"engine",engine)
    with Session(engine) as session:
        session.add(WorkbookDocument(id="23",numeric_id=23,title="第50届觉观"))
        session.add(ResourceIdentity(id=26,resource_type="note",legacy_pk="unrelated"))
        session.add(ResourceIdentity(id=100,resource_type="note",legacy_pk="another-note"))
        session.add(ResourceAccessGrant(resource_type="workbook",resource_id="23",subject_key="anonymous",subject_type="anonymous",role="viewer"))
        session.add(ResourceAccessGrant(resource_type="sheet",resource_id="90",subject_key="anonymous",subject_type="anonymous",role="editor"))
        session.commit()
    sheet={"id":100,"legacy_id":"course-sheet","title":"考勤表","scope":"notes","engine":"handsontable",
           "owner_type":"course_workbook","owner_key":"20261001-jueguan-51","sheet_key":"attendance"}
    def bundle(wid):
        if wid==23:return {"sheets":[{"id":90,"sheet_key":"attendance"}]}
        return {"id":26,"title":"第51届觉观","legacy_id":"course-workbook","sheets":[deepcopy(sheet)]}
    monkeypatch.setattr(client,"LocalAttendanceSheetClient",lambda:type("Client",(),{"get_workbook_document":staticmethod(bundle)})())
    monkeypatch.setattr(monthly_courses,"list_monthly_course_manifests",lambda:[{"courses":[{"workbook_id":26,"source_workbook_id":23}]}])
    remaps=[]
    def assign(**kwargs):
        remaps.append(kwargs)
        sheet["id"]=kwargs["sheet_routes"][100]
    monkeypatch.setattr(monthly_courses,"assign_monthly_sheet_routes",assign)
    assert workbook_registry.reconcile_monthly_course_registry()==[26]
    assert sheet["id"]!=100 and len(remaps)==1
    assert workbook_registry.reconcile_monthly_course_registry()==[]
    with Session(engine) as session:
        created=session.exec(select(SheetDocument)).one()
        assert created.document_json=={}
        assert created.numeric_id==sheet["id"]
        assert session.get(ResourceIdentity,100).legacy_pk=="another-note"
        assert len(session.exec(select(ResourceAccessGrant)).all())==4
