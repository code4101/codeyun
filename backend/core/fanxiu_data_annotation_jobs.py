"""Legacy compatibility alias for Fanxiu data-annotation job definitions.

Canonical implementation lives in ``backend.core.fanxiu.data_annotation.jobs``.
"""

import sys

from backend.core.fanxiu.data_annotation import jobs as _module

sys.modules[__name__] = _module
