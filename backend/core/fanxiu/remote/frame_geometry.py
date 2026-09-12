"""Map a full portrait game framebuffer to the existing annotation canvas.

Equal aspect ratio is necessary, not proof of equal UI layout. The recognizer
must still identify the normalized scene before an asset can authorize a click.
Letterboxing, crops and changed aspect ratios need a separate viewport adapter.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FrameGeometry:
    width: int
    height: int
    reference_width: int
    reference_height: int

    @property
    def compatible(self) -> bool:
        return (self.width >= 540 and self.height >= 960
                and self.width * self.reference_height == self.height * self.reference_width)

    def original_point(self, x: int, y: int) -> tuple[int, int]:
        """Nearest original pixel, with only the final boundary pixel clamped."""
        if not self.compatible or not (0 <= x < self.reference_width and 0 <= y < self.reference_height):
            raise ValueError("点位不属于已验证的参考画布")
        return (min(self.width - 1, math.floor(x * self.width / self.reference_width + .5)),
                min(self.height - 1, math.floor(y * self.height / self.reference_height + .5)))

    def normalize_image(self, image):
        """Normalize a decoded full framebuffer; never stretch another ratio."""
        from PIL import Image
        if not self.compatible or image.size != (self.width, self.height):
            raise ValueError("截图尺寸不支持等比例参考画布映射")
        result = image.convert("RGB")
        if result.size != (self.reference_width, self.reference_height):
            result = result.resize((self.reference_width, self.reference_height), Image.Resampling.LANCZOS)
        return result

    def metadata(self) -> dict:
        return {"source_width": self.width, "source_height": self.height,
                "reference_width": self.reference_width, "reference_height": self.reference_height,
                "scale_x": self.reference_width / self.width,
                "scale_y": self.reference_height / self.height,
                "mode": "full_frame_uniform_scale", "action_coordinates": "source_pixels"}
