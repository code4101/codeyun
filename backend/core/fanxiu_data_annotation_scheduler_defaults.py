"""Legacy compatibility alias for Fanxiu scheduler default task definitions.

Canonical implementation lives in
``backend.core.fanxiu.data_annotation.scheduler_defaults``.
"""

import sys

from backend.core.fanxiu.data_annotation import scheduler_defaults as _module

sys.modules[__name__] = _module
