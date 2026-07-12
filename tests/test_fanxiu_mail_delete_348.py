from backend.core.fanxiu.data_annotation.runtime_runner import DataAnnotationRuntimeRunner


class _Runtime:
    def __init__(self):
        self.clicks = []

    def click_shape(self, scene_id, shape, **kwargs):
        self.clicks.append((scene_id, shape, kwargs.get("frame_data_url")))


def test_mail_delete_348_is_business_confirm_not_cancel():
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    runtime = _Runtime()

    runner._click_confirmed_mail_delete_prompt(runtime, 348, frame_data_url="frame348")

    assert runtime.clicks == [(348, "\u786e\u8ba4", "frame348")]
