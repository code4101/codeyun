"""Thin adapters around the running, logged-in official Baidu Netdisk client.

The official client performs every transfer.  This package only provides the
pieces CodeYun owns around it: a loopback health check of the official bridge,
local download-directory manifests, single-step collection of a finished
download into an explicit business directory, and a submission ledger that
prevents resubmitting a transfer whose outcome is still unknown.
"""

from .bridge import ClientVersion, probe_client_version
from .ledger import TransferLedger, transfer_key
from .local_files import (
    CollectResult,
    DirCollectResult,
    FileFact,
    collect_download,
    collect_download_dir,
    describe_upload_tree,
    describe_upload_source,
    load_manifest,
    size_label_matches,
    snapshot_dir,
    validate_file_name,
    validate_remote_dir,
    write_manifest,
)

__all__ = [
    "ClientVersion",
    "CollectResult",
    "DirCollectResult",
    "FileFact",
    "TransferLedger",
    "collect_download",
    "collect_download_dir",
    "describe_upload_tree",
    "describe_upload_source",
    "load_manifest",
    "size_label_matches",
    "probe_client_version",
    "snapshot_dir",
    "transfer_key",
    "validate_file_name",
    "validate_remote_dir",
    "write_manifest",
]
