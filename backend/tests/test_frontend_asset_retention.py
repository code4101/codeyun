import json
import subprocess
import sys

import pytest
from backend.core.runtime.frontend_asset_retention import retain_frontend_assets, asset_retention_program


def test_old_tabs_survive_multiple_releases_and_current_entry_is_preserved(tmp_path):
    releases = tmp_path / 'releases'
    for name in ('first', 'middle', 'current'):
        (releases / name / 'assets').mkdir(parents=True)
        (releases / name / 'index.html').write_text(name)
    (releases / 'first/assets/pdf-old.js').write_text('old PDF module')
    (releases / 'middle/assets/book-old.js').write_text('old book module')
    (releases / 'middle/assets/common.js').write_text('old common')
    (releases / 'current/assets/common.js').write_text('current common')
    target = releases / 'current'
    program = asset_retention_program(str(tmp_path), str(target))
    result = subprocess.run([sys.executable, '-c', program], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)['retained_assets'] == 2
    assert (target / 'assets/pdf-old.js').read_text() == 'old PDF module'
    assert (target / 'assets/book-old.js').read_text() == 'old book module'
    assert (target / 'assets/common.js').read_text() == 'current common'
    assert (target / 'index.html').read_text() == 'current'
    assert retain_frontend_assets(str(tmp_path), str(target))['retained_assets'] == 0


def test_nested_viewer_assets_and_path_boundary(tmp_path):
    old = tmp_path / 'releases/old/plugins/project-graph/assets'
    old.mkdir(parents=True)
    (old / 'module.js').write_text('module')
    target = tmp_path / 'releases/new'
    target.mkdir()
    retain_frontend_assets(str(tmp_path), str(target))
    assert (target / 'plugins/project-graph/assets/module.js').read_text() == 'module'
    with pytest.raises(ValueError):
        retain_frontend_assets(str(tmp_path), str(tmp_path))
