"""In-process CodeYun job scheduling and execution.

Jobs are Python callables executed by the backend.  Process lifecycle belongs
to :mod:`backend.core.services`, never to this package.
"""

