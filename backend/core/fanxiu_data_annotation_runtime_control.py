"""Legacy compatibility alias for Fanxiu data-annotation runtime control.

Canonical implementation lives in
``backend.core.fanxiu.data_annotation.runtime_control``.
"""

import sys

from backend.core.fanxiu.data_annotation import runtime_control as _module

sys.modules[__name__] = _module
