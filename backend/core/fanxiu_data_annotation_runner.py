"""Legacy compatibility alias for Fanxiu data-annotation runner helpers.

Canonical implementation lives in ``backend.core.fanxiu.data_annotation.runner``.
"""

import sys

from backend.core.fanxiu.data_annotation import runner as _module

sys.modules[__name__] = _module
