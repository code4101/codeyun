"""下载入库前的本地人物粗筛；不调用大模型，不识别身份、性别或颜值。

当前采用 YuNet 检测可见人脸：背影、遮挡和极小人物可能漏检，这是低成本
粗筛的取舍。采集器先在内存中检查下载字节，仅 keep=True 才进入持久化流程。
模型缺失或推理异常向调用方报错，不能退化成无条件保存。此模块不自行联网。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock


@dataclass(frozen=True)
class PersonFilterResult:
    keep: bool
    face_count: int
    best_score: float
    reason: str


class FacePresenceFilter:
    """复用单个 CPU YuNet 实例；缩至最长边 640，只判断是否存在清晰人脸。

    threshold 是模型分数阈值，并非准确率。默认 0.8；非人物图片、破损图片
    返回 keep=False。不要将结果解释为“美女”标签。
    """

    def __init__(self, model_path: str | Path | None = None, *, threshold: float = 0.8):
        import cv2

        if not 0 < threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")
        if model_path is None:
            from backend.core.settings import get_settings

            model_path = get_settings().data_dir / "models" / "opencv" / "face_detection_yunet_2023mar.onnx"
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"YuNet model not installed: {path}")
        self._detector = cv2.FaceDetectorYN.create(
            str(path), "", (640, 640), threshold, 0.3, 5000,
            cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU,
        )
        self._lock = Lock()

    def inspect_bytes(self, data: bytes) -> PersonFilterResult:
        """检查下载字节，不写文件；推理异常保留为显式错误供采集器停止/记录。"""
        import cv2
        import numpy as np

        if not data:
            return PersonFilterResult(False, 0, 0.0, "invalid_image")
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return PersonFilterResult(False, 0, 0.0, "invalid_image")
        height, width = image.shape[:2]
        scale = min(1.0, 640 / max(height, width))
        if scale < 1:
            image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
        with self._lock:
            self._detector.setInputSize((image.shape[1], image.shape[0]))
            _, faces = self._detector.detect(image)
        if faces is None or not len(faces):
            return PersonFilterResult(False, 0, 0.0, "no_face")
        return PersonFilterResult(True, len(faces), float(faces[:, -1].max()), "face_detected")
