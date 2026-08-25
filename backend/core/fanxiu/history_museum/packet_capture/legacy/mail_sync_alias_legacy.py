"""Legacy compatibility alias for Fanxiu mail packet sync helpers.

Canonical implementation lives in ``backend.core.fanxiu.mail.packet_sync``.
"""

import sys

from backend.core.fanxiu.history_museum.packet_capture import mail_sync as _module

sys.modules[__name__] = _module
