"""Image transfer regression tests never touch the real desktop clipboard."""
import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import desktop_windows as api
from backend.core.devices import window_projection as projection


def raster(format='PNG'):
    out = BytesIO()
    Image.new('RGB', (12, 8), (30, 80, 120)).save(out, format=format)
    return out.getvalue()


def image_url():
    return 'data:image/png;base64,' + base64.b64encode(raster()).decode()


def fake_clipboard(monkeypatch, data=None):
    import win32clipboard
    calls = []
    data = {} if data is None else data
    monkeypatch.setattr(win32clipboard, 'OpenClipboard', lambda: calls.append('open'))
    monkeypatch.setattr(win32clipboard, 'CloseClipboard', lambda: calls.append('close'))
    monkeypatch.setattr(win32clipboard, 'EmptyClipboard', lambda: data.clear())
    monkeypatch.setattr(win32clipboard, 'RegisterClipboardFormat', lambda _: 50000)
    monkeypatch.setattr(win32clipboard, 'IsClipboardFormatAvailable', lambda fmt: fmt in data)
    monkeypatch.setattr(win32clipboard, 'GetClipboardData', lambda fmt: data[fmt])
    monkeypatch.setattr(win32clipboard, 'SetClipboardData', lambda fmt, raw: data.__setitem__(fmt, raw))
    return calls, data


def test_png_and_native_dib_roundtrip(monkeypatch):
    import win32con
    calls, data = fake_clipboard(monkeypatch)
    png = projection.clipboard_image_bytes(image_url())
    projection.write_clipboard_image(png)
    assert set(data) == {50000, win32con.CF_DIB}
    for format in (50000, win32con.CF_DIB):
        payload = data[format]
        data.clear(); data[format] = payload
        result = projection.projection_clipboard_image()
        with Image.open(BytesIO(base64.b64decode(result['image'].split(',')[1]))) as image:
            assert image.size == (12, 8)
            assert image.getpixel((0, 0))[:3] == (30, 80, 120)
        if format == 50000:
            # Restore the native DIB variant for the second iteration.
            projection.write_clipboard_image(png)
    assert calls.count('open') == calls.count('close')


def test_missing_clipboard_image_closes_clipboard(monkeypatch):
    calls, _ = fake_clipboard(monkeypatch)
    with pytest.raises(ValueError, match='没有图片'):
        projection.projection_clipboard_image()
    assert calls == ['open', 'close']


def test_unicode_typing_sends_utf16_without_clipboard(monkeypatch):
    import ctypes, win32gui
    captured = []
    class Sender:
        def __call__(self, count, events, size):
            assert size == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
            captured.extend((event.union.keyboard.scan, event.union.keyboard.flags) for event in events)
            return count
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **kw: SimpleNamespace(SendInput=Sender()))
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: 123)
    projection.type_projection_text(123, 'A中🙂')
    assert captured == [(65, 4), (65, 6), (0x4e2d, 4), (0x4e2d, 6),
                        (0xd83d, 4), (0xd83d, 6), (0xde42, 4), (0xde42, 6)]
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: 456)
    with pytest.raises(RuntimeError, match='焦点改变'):
        projection.type_projection_text(123, 'x')
    assert len(captured) == 8


def test_type_uses_current_caret_without_paste(monkeypatch):
    import win32clipboard
    calls = []
    monkeypatch.setattr(projection, 'selected_window', lambda _: SimpleNamespace(hwnd=123))
    monkeypatch.setattr(projection, '_window_identity', lambda _: 'target')
    monkeypatch.setattr(projection, 'get_capture_rect', lambda *a: (0, 0, 200, 100))
    monkeypatch.setattr(projection, 'focus_projection_window', lambda _: None)
    monkeypatch.setattr(projection, 'click_window_raw_point', lambda *a: pytest.fail('typing must preserve caret'))
    monkeypatch.setattr(win32clipboard, 'OpenClipboard', lambda: pytest.fail('typing must preserve clipboard'))
    monkeypatch.setattr(projection, 'type_projection_text', lambda hwnd, text: calls.append((hwnd, text)))
    projection.projection_input(window_id='target', token=projection.input_token('target', 200, 100), action='type', text='中文🙂')
    assert calls == [(123, '中文🙂')]
    for text in ('', 'a' * 4097):
        with pytest.raises(ValueError):
            api.WindowInput(window_id='target', token='token', action='type', text=text)


@pytest.mark.parametrize('payload', ['', 'data:image/png;base64,???', 'data:image/png;base64,dGV4dA==', 'data:image/svg+xml;base64,PHN2Zz4='])
def test_invalid_image_rejected_before_focus(monkeypatch, payload):
    monkeypatch.setattr(projection, 'selected_window', lambda _: pytest.fail('must not focus'))
    with pytest.raises(ValueError, match='图片无效'):
        projection.projection_input(window_id='target', token='token', action='image', image=payload)


def test_pixel_and_byte_limits(monkeypatch):
    monkeypatch.setattr(projection, 'MAX_CLIPBOARD_IMAGE_PIXELS', 20)
    with pytest.raises(ValueError, match='图片无效'):
        projection.clipboard_image_bytes(image_url())
    monkeypatch.setattr(projection, 'MAX_CLIPBOARD_IMAGE_BYTES', 10)
    with pytest.raises(ValueError, match='图片无效'):
        projection.clipboard_image_bytes(image_url())


@pytest.mark.parametrize('action', ['image', 'paste'])
def test_paste_image_uses_current_caret_or_legacy_point_once(monkeypatch, action):
    import win32api, win32gui, win32con
    keys, clicks = [], []
    calls, data = fake_clipboard(monkeypatch)
    monkeypatch.setattr(projection, 'selected_window', lambda _: SimpleNamespace(hwnd=123))
    monkeypatch.setattr(projection, '_window_identity', lambda _: 'target')
    monkeypatch.setattr(projection, 'get_capture_rect', lambda *a: (0, 0, 200, 100))
    monkeypatch.setattr(projection, 'focus_projection_window', lambda _: None)
    monkeypatch.setattr(projection, 'click_window_raw_point', lambda *a: clicks.append(a[-1]))
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: 123)
    monkeypatch.setattr(win32api, 'keybd_event', lambda key, scan, flag, extra: keys.append((key, flag)))
    result = projection.projection_input(window_id='target', token=projection.input_token('target', 200, 100),
                                         action=action, image=image_url(), x=.3, y=.4)
    assert result['accepted']
    assert clicks == ([(60, 40)] if action == 'image' else [])
    assert keys == [(win32con.VK_CONTROL, 0), (ord('V'), 0), (ord('V'), win32con.KEYEVENTF_KEYUP),
                    (win32con.VK_CONTROL, win32con.KEYEVENTF_KEYUP)]
    assert calls == ['open', 'close'] and data


@pytest.mark.parametrize('kind', ['text', 'image'])
def test_copy_returns_new_selection_without_clicking(monkeypatch, kind):
    import win32api, win32gui, win32clipboard, win32con
    _, data = fake_clipboard(monkeypatch, {win32con.CF_UNICODETEXT: 'old clipboard'})
    sequence = [100]
    keys = []
    monkeypatch.setattr(win32clipboard, 'GetClipboardSequenceNumber', lambda: sequence[0])
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: 123)
    def press(key, scan, flags, extra):
        keys.append((key, flags))
        if key == ord('C') and not flags:
            data.clear()
            data[win32con.CF_UNICODETEXT if kind == 'text' else 50000] = 'new selection' if kind == 'text' else raster()
            sequence[0] += 1
    monkeypatch.setattr(win32api, 'keybd_event', press)
    result = projection.copy_projection_selection(123)
    if kind == 'text':
        assert result == {'text': 'new selection'}
    else:
        with Image.open(BytesIO(base64.b64decode(result['image'].split(',')[1]))) as image:
            assert image.size == (12, 8) and image.getpixel((0, 0))[:3] == (30, 80, 120)
    assert keys.count((ord('C'), 0)) == 1


def test_copy_never_returns_old_clipboard_on_timeout(monkeypatch):
    import win32clipboard, win32gui, win32api
    monkeypatch.setattr(win32clipboard, 'GetClipboardSequenceNumber', lambda: 100)
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: 123)
    monkeypatch.setattr(win32api, 'keybd_event', lambda *args: None)
    ticks = iter([0, 0, 2])
    monkeypatch.setattr(projection.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(projection.time, 'sleep', lambda _: None)
    monkeypatch.setattr(projection, 'read_projection_clipboard', lambda: pytest.fail('old clipboard must not be read'))
    with pytest.raises(RuntimeError, match='未更新剪贴板'):
        projection.copy_projection_selection(123)


def test_image_api_requires_owner_and_payload(monkeypatch):
    app = FastAPI(); app.include_router(api.router, prefix='/windows')
    app.dependency_overrides[api.get_current_user_from_token] = lambda: SimpleNamespace(is_superuser=False)
    calls = []
    monkeypatch.setattr(projection, 'projection_clipboard_image', lambda: {'image': image_url()})
    monkeypatch.setattr(projection, 'projection_input', lambda **kw: calls.append(kw) or {'accepted': True})
    with TestClient(app) as client:
        assert client.get('/windows/clipboard/image').status_code == 403
        app.dependency_overrides[api.get_current_user_from_token] = lambda: SimpleNamespace(is_superuser=True)
        assert client.get('/windows/clipboard/image').json()['image'] == image_url()
        payload = {'window_id': 'target', 'token': 'signed', 'action': 'image'}
        assert client.post('/windows/input', json=payload).status_code == 422
        assert not calls
        assert client.post('/windows/input', json={**payload, 'image': image_url()}).status_code == 200
        assert len(calls) == 1 and calls[0]['image'] == image_url()
