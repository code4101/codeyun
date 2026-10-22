import io
from datetime import datetime
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from openpyxl import Workbook
from backend.core.excel_import import load_import_workbook


def test_empty_fill_export_preserves_names_dates_and_text():
    book = Workbook()
    book.active.title = '考勤名单'
    book.active.append(['姓名', '开课日期', '手机号'])
    book.active.append(['甲', datetime(2026, 10, 19), '13800000000'])
    source = io.BytesIO()
    book.save(source)
    damaged = io.BytesIO()
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with ZipFile(source) as zin, ZipFile(damaged, 'w') as zout:
        for entry in zin.infolist():
            data = zin.read(entry.filename)
            if entry.filename == 'xl/styles.xml':
                styles = ET.fromstring(data)
                styles.find(f'{ns}fills/{ns}fill').clear()
                data = ET.tostring(styles)
            zout.writestr(entry, data)
    restored = load_import_workbook(damaged.getvalue())
    assert restored.sheetnames == ['考勤名单']
    assert list(restored.active.values)[1] == ('甲', datetime(2026, 10, 19), '13800000000')
    restored.close()
