"""Legacy compatibility alias for Fanxiu modao/shouyuan OCR helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.modao_shouyuan_ocr``.
"""

import sys

from backend.core.fanxiu.catalog import modao_shouyuan_ocr as _module

sys.modules[__name__] = _module
