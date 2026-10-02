import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
from backend.core.devices import window_projection as projection
from backend.api import codex_sessions as api
from backend.api import desktop_windows as windows_api


def test_unchanged_frame_sends_no_image_and_small_change_is_a_tile():
    first = np.zeros((600, 800, 3), dtype=np.uint8)
    assert len(projection.changed_tiles(first, None)) == 1
    assert projection.changed_tiles(first, first.copy()) == []
    second = first.copy(); second[300, 300] = 255
    patch = projection.changed_tiles(second, first)
    assert len(patch) == 1
    assert (patch[0]['x'], patch[0]['y'], patch[0]['width']) == (256, 256, 256)
    assert patch[0]['image'].startswith('data:image/webp;base64,')


def test_projection_owner_protection_and_ambiguous_input_is_not_retried(monkeypatch):
    app = FastAPI(); app.include_router(api.router, prefix='/api/codex')
    app.dependency_overrides[api.get_current_user_from_token] = lambda: SimpleNamespace(is_superuser=False)
    calls = []
    def fail(**kwargs):
        calls.append(kwargs); raise RuntimeError('focus changed')
    monkeypatch.setattr(projection, 'projection_input', fail)
    with TestClient(app) as client:
        assert client.get('/api/codex/desktop/window/frame').status_code == 403
        app.dependency_overrides[api.get_current_user_from_token] = lambda: SimpleNamespace(is_superuser=True)
        assert client.post('/api/codex/desktop/window/input', json={'token':'t','action':'click','x':2}).status_code == 422
        assert client.post('/api/codex/desktop/window/input', json={'token':'t','action':'text','text':'hi','send':True}).status_code == 409
        assert len(calls) == 1


def test_evicted_pixel_cache_does_not_expire_input_authorization(monkeypatch):
    token=projection.input_token('window-process',20,10)
    monkeypatch.setattr(projection,'_frames',{})
    projection.validate_input_token(token,'window-process',20,10)
    with pytest.raises(ValueError,match='切换'):
        projection.validate_input_token(token,'another-process',20,10)
    with pytest.raises(ValueError,match='连接'):
        projection.validate_input_token(token+'tampered','window-process',20,10)


def test_resized_window_stops_before_click(monkeypatch):
    monkeypatch.setattr(projection, '_codex_window', lambda: SimpleNamespace(hwnd=123))
    monkeypatch.setattr(projection, '_window_identity', lambda hwnd:'window-process')
    monkeypatch.setattr(projection, 'click_window_raw_point', lambda *a: pytest.fail('must not click'))
    monkeypatch.setattr(projection, 'get_capture_rect', lambda *a:(0,0,30,10))
    with pytest.raises(ValueError, match='尺寸'):
        projection.projection_input(token=projection.input_token('window-process',20,10), action='click')


def test_foreground_queue_is_detached_even_when_focus_fails(monkeypatch):
    import win32api
    import win32gui
    import win32process
    calls = []
    monkeypatch.setattr(projection, 'activate_window', lambda hwnd: None)
    monkeypatch.setattr(win32api, 'GetCurrentThreadId', lambda: 10)
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: 1)
    monkeypatch.setattr(win32process, 'GetWindowThreadProcessId', lambda hwnd: (hwnd + 10, 100))
    monkeypatch.setattr(win32process, 'AttachThreadInput', lambda a,b,joined: calls.append((b,joined)))
    monkeypatch.setattr(win32gui, 'BringWindowToTop', lambda hwnd: None)
    def fail(hwnd):
        raise RuntimeError('focus denied')
    monkeypatch.setattr(win32gui, 'SetForegroundWindow', fail)
    with pytest.raises(RuntimeError, match='focus denied'):
        projection.focus_projection_window(2)
    assert {thread for thread,joined in calls if joined} == {11,12}
    assert {thread for thread,joined in calls if not joined} == {11,12}


def test_capture_dpi_context_is_set_on_every_worker_call(monkeypatch):
    from backend.core.devices import window_capture_preview as capture
    calls = []
    monkeypatch.setattr(capture.ctypes.windll.user32, 'SetThreadDpiAwarenessContext', lambda value: calls.append(value.value))
    monkeypatch.setattr(capture.ctypes.windll.shcore, 'SetProcessDpiAwareness', lambda value: None)
    capture.set_dpi_awareness()
    capture.set_dpi_awareness()
    assert calls == [capture.ctypes.c_void_p(-4).value] * 2


def test_generic_window_api_targets_selected_application_once(monkeypatch):
    app = FastAPI(); app.include_router(windows_api.router, prefix='/api/desktop-windows')
    app.dependency_overrides[windows_api.get_current_user_from_token] = lambda: SimpleNamespace(is_superuser=False)
    calls = []
    monkeypatch.setattr(projection, 'list_projection_windows', lambda: [{'id':'notepad-process','application':'notepad.exe'}])
    monkeypatch.setattr(projection, 'projection_frame', lambda token, window_id: {'window_id':window_id})
    monkeypatch.setattr(projection, 'projection_input', lambda **kwargs: calls.append(kwargs) or {'accepted':True})
    with TestClient(app) as client:
        assert client.get('/api/desktop-windows').status_code == 403
        app.dependency_overrides[windows_api.get_current_user_from_token] = lambda: SimpleNamespace(is_superuser=True)
        assert client.get('/api/desktop-windows').json()['windows'][0]['application'] == 'notepad.exe'
        assert client.get('/api/desktop-windows/frame', params={'window_id':'notepad-process'}).json()['window_id'] == 'notepad-process'
        result = client.post('/api/desktop-windows/input', json={'window_id':'notepad-process','token':'signed','action':'text','text':'多行\n中文','send':True,'send_key':'ctrl_enter'})
        assert result.status_code == 200
        assert len(calls) == 1
        assert calls[0]['window_id'] == 'notepad-process'
        assert calls[0]['text'] == '多行\n中文'
        assert calls[0]['send_key'] == 'ctrl_enter'


def test_closed_or_excluded_window_is_rejected_before_input(monkeypatch):
    monkeypatch.setattr(projection, 'list_projection_windows', lambda: [])
    monkeypatch.setattr(projection, 'focus_projection_window', lambda hwnd: pytest.fail('must not focus'))
    with pytest.raises(ValueError, match='窗口已关闭'):
        projection.projection_input(window_id='123:closed-process',token='old',action='click')


def test_accelerated_window_uses_guarded_screen_fallback(monkeypatch):
    import mss
    from contextlib import nullcontext
    checks = []
    pixels = np.ones((20,30,4), dtype=np.uint8) * 100
    monkeypatch.setattr(projection, 'capture_by_printwindow', lambda *args: None)
    monkeypatch.setattr(projection, 'require_uncovered_window', lambda hwnd: checks.append(hwnd))
    monkeypatch.setattr(mss, 'mss', lambda: nullcontext(object()))
    monkeypatch.setattr(projection, 'capture_by_screen', lambda *args: pixels)
    frame, mode = projection.capture_projection_window(123)
    assert frame is pixels and mode == 'screen'
    assert checks == [123,123]


def test_screen_fallback_never_returns_occluding_app_pixels(monkeypatch):
    monkeypatch.setattr(projection, 'capture_by_printwindow', lambda *args: None)
    def blocked(hwnd):
        raise RuntimeError('窗口被遮挡')
    monkeypatch.setattr(projection, 'require_uncovered_window', blocked)
    monkeypatch.setattr(projection, 'capture_by_screen', lambda *args: pytest.fail('must not capture another app'))
    with pytest.raises(RuntimeError, match='遮挡'):
        projection.capture_projection_window(123)


def test_window_selection_activates_before_capturing(monkeypatch):
    calls = []
    monkeypatch.setattr(projection, 'selected_window', lambda identity: SimpleNamespace(hwnd=123))
    monkeypatch.setattr(projection, 'focus_projection_window', lambda hwnd: calls.append(hwnd))
    monkeypatch.setattr(projection, '_window_identity', lambda hwnd: '123:process')
    monkeypatch.setattr(projection.time, 'sleep', lambda duration: None)
    result = projection.activate_projection_window('123:process')
    assert result['activated'] and calls == [123]


@pytest.mark.parametrize('button,action,expected', [
    ('left','click',[2,4]), ('right','click',[8,16]),
    ('middle','click',[32,64]), ('left','double_click',[2,4,2,4]),
])
def test_pointer_buttons_and_double_click_are_atomic(monkeypatch,button,action,expected):
    import win32api, win32gui
    flags, positions = [], []
    monkeypatch.setattr(win32gui,'GetForegroundWindow',lambda:123)
    monkeypatch.setattr(win32api,'SetCursorPos',lambda point:positions.append(point))
    monkeypatch.setattr(win32api,'mouse_event',lambda flag,*args:flags.append(flag))
    monkeypatch.setattr(projection.time,'sleep',lambda duration:None)
    projection.pointer_gesture(123,(-100,200,100,400),action,button,.5,.25,[],0)
    assert flags == expected
    assert all(point== (0,250) for point in positions)


def test_drag_replays_path_and_always_releases_on_failure(monkeypatch):
    import win32api, win32gui, win32con
    flags,positions=[],[]
    monkeypatch.setattr(win32gui,'GetForegroundWindow',lambda:123)
    def move(point):
        positions.append(point)
        if len(positions)==3: raise RuntimeError('device lost')
    monkeypatch.setattr(win32api,'SetCursorPos',move)
    monkeypatch.setattr(win32api,'mouse_event',lambda flag,*args:flags.append(flag))
    monkeypatch.setattr(projection.time,'sleep',lambda duration:None)
    with pytest.raises(RuntimeError,match='device lost'):
        projection.pointer_gesture(123,(0,0,100,100),'drag','left',0,0,[{'x':0,'y':0},{'x':.5,'y':.5},{'x':1,'y':1}],0)
    assert positions == [(0,0),(50,50),(99,99)]
    assert flags == [win32con.MOUSEEVENTF_LEFTDOWN,win32con.MOUSEEVENTF_LEFTUP]


def test_drag_schema_validates_path_before_dispatch(monkeypatch):
    app = FastAPI(); app.include_router(windows_api.router, prefix='/windows')
    app.dependency_overrides[windows_api.get_current_user_from_token] = lambda: SimpleNamespace(is_superuser=True)
    calls=[]
    monkeypatch.setattr(projection,'projection_input',lambda **kwargs:calls.append(kwargs) or {'accepted':True})
    base={'window_id':'target','token':'signed','action':'drag'}
    with TestClient(app) as client:
        assert client.post('/windows/input',json=base).status_code == 422
        assert client.post('/windows/input',json={**base,'path':[{'x':0,'y':0},{'x':2,'y':1}]}).status_code == 422
        assert not calls
        assert client.post('/windows/input',json={**base,'button':'right','path':[{'x':0,'y':0},{'x':1,'y':1}]}).status_code == 200
        assert len(calls)==1 and calls[0]['button']=='right'
        assert calls[0]['path']==[{'x':0,'y':0},{'x':1,'y':1}]


def test_restore_uses_window_switch_when_direct_restore_is_rejected(monkeypatch):
    from backend.core.devices import window_capture_preview as capture
    minimized=[True]; calls=[]
    monkeypatch.setattr(capture.win32gui,'IsIconic',lambda hwnd:minimized[0])
    monkeypatch.setattr(capture.time,'sleep',lambda duration:None)
    monkeypatch.setattr(capture.ctypes.windll.user32,'ShowWindowAsync',lambda hwnd,command:calls.append('restore') or 0)
    def switch(hwnd,alt_tab):
        calls.append('switch'); minimized[0]=False
    monkeypatch.setattr(capture.ctypes.windll.user32,'SwitchToThisWindow',switch)
    capture.restore_window(123)
    assert calls==['restore','switch'] and not minimized[0]


def test_frame_restores_minimized_window_before_capture(monkeypatch):
    calls=[]
    monkeypatch.setattr(projection,'selected_window',lambda identity:SimpleNamespace(hwnd=123,title='test'))
    monkeypatch.setattr(projection,'restore_window',lambda hwnd:calls.append('restore'))
    pixels=np.zeros((20,30,4),dtype=np.uint8); pixels[0,0,0]=100
    def capture(hwnd):
        assert calls==['restore']; calls.append('capture'); return pixels,'window'
    monkeypatch.setattr(projection,'capture_projection_window',capture)
    monkeypatch.setattr(projection,'_window_identity',lambda hwnd:'123:process')
    monkeypatch.setattr(projection,'_frames',{})
    assert projection.projection_frame(window_id='123:process')['width']==30
    assert calls==['restore','capture']
