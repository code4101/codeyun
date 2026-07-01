"""Legacy compatibility alias for Fanxiu default runtime job registration.

Canonical implementation lives in
``backend.core.fanxiu.data_annotation.default_jobs``.
"""

import sys

from backend.core.fanxiu.data_annotation import default_jobs as _module

sys.modules[__name__] = _module
