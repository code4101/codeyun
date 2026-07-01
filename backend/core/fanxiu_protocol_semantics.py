"""Legacy compatibility alias for Fanxiu protocol semantics helpers.

Canonical implementation lives in ``backend.core.fanxiu.catalog.protocol_semantics``.
"""

import sys

from backend.core.fanxiu.catalog import protocol_semantics as _module

sys.modules[__name__] = _module
