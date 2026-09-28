"""Owner-scoped library storage paths shared by writers and usage reporting.

These accessors do not create directories or read book content.
"""
from pathlib import Path
from backend.core.settings import get_settings

PDF_HOSTED_ENTRY_ID = "codeyun-pdf-store"


def library_owner_roots(owner_id: int) -> tuple[Path, Path]:
    if owner_id <= 0:
        raise ValueError("owner_id must be positive")
    root = get_settings().data_dir
    return (root / "pdf-documents" / f"user_{owner_id}",
            root / "library-books" / f"user_{owner_id}")


def library_book_path(owner_id: int, topic_id: int) -> Path:
    return library_owner_roots(owner_id)[1] / "linux-do" / str(int(topic_id)) / "book.json"
