"""Open media download primitives used by CodeYun.

The package intentionally contains no discovery, recommendation, keyword search,
or access-control bypass.  A caller must provide an explicit media URL and the
downloader only uses streams that the current browser session may normally play.
"""

from .batch import VIDEO_REVIEW_LIMIT, refill_video_review_batch, video_roots
from .bilibili import (
    BilibiliDownloadResult,
    download_bilibili_media,
    parse_bvid,
    refresh_bilibili_result_path,
)
from .html_document import video_document_path, write_video_html_document
from .douyin import DouyinDownloadResult, download_douyin_media, parse_douyin_video_id

__all__ = [
    "BilibiliDownloadResult",
    "DouyinDownloadResult",
    "VIDEO_REVIEW_LIMIT",
    "download_bilibili_media",
    "download_douyin_media",
    "parse_bvid",
    "parse_douyin_video_id",
    "refresh_bilibili_result_path",
    "refill_video_review_batch",
    "video_document_path",
    "video_roots",
    "write_video_html_document",
]
