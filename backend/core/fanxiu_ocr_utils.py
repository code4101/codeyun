"""Legacy compatibility alias for Fanxiu OCR helper utilities.

Canonical implementation lives in ``backend.core.fanxiu.game.ocr_utils``.
"""

import sys

from backend.core.fanxiu.game import ocr_utils as _module

sys.modules[__name__] = _module
