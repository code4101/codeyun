"""Compatibility alias for the Fanxiu Kernel scheduler control plane.

Canonical implementation lives in
``backend.core.fanxiu.data_annotation.kernel_scheduler_control``.
"""

import sys

from backend.core.fanxiu.data_annotation import kernel_scheduler_control as _module

sys.modules[__name__] = _module
