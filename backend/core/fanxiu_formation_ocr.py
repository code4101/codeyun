"""Legacy compatibility alias for Fanxiu formation OCR helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.formation_ocr``.
"""

import sys

from backend.core.fanxiu.catalog import formation_ocr as _module

sys.modules[__name__] = _module
