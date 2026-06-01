"""Deprecated shim for the old attendance cell_meta link repair.

Hyperlinks are now stored as inline cell values. Run the canonical migration
instead of shifting legacy ``cell_meta.link`` entries.
"""

from __future__ import annotations

from migrate_note_sheet_links_to_inline import migrate_note_sheet_links_to_inline


def repair() -> None:
    stats = migrate_note_sheet_links_to_inline(dry_run=False)
    print(
        "migrated inline links: "
        f"scanned={stats['scanned']}, updated={stats['updated']}, "
        f"legacy_links={stats['legacy_links']}, stripped_meta={stats['stripped_meta']}"
    )


if __name__ == "__main__":
    repair()
