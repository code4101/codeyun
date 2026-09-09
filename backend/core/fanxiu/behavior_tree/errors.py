from __future__ import annotations


class BehaviorTreeExecutionError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


class SceneClickMismatch(RuntimeError):
    """The pre-click guard recognized another scene; target input was not sent.

    Callers may re-observe an explicitly supported transition, but must not
    treat this as a retryable failure of an already submitted action.
    """

    def __init__(self, message: str, *, expected_scene_id: int, actual_scene_id: int) -> None:
        super().__init__(message)
        self.expected_scene_id = int(expected_scene_id)
        self.actual_scene_id = int(actual_scene_id)
