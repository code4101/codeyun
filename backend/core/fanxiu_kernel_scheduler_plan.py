"""Compatibility alias for Fanxiu Kernel scheduler plan helpers.

Canonical implementation lives in ``backend.core.fanxiu.data_annotation.kernel_scheduler_plan``.
"""

import sys

from backend.core.fanxiu.data_annotation import kernel_scheduler_plan as _module

sys.modules[__name__] = _module
