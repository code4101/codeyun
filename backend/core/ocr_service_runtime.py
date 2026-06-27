import sys

from backend.core.runtime import ocr_service as _module

sys.modules[__name__] = _module
