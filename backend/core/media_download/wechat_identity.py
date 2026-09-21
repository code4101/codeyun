"""微信图片来源身份契约，与页面采集、登录账号、存储数据库解耦。

公众号 bizuin（文章 __biz 解码所得）+ mid + idx 标识文章，不能使用昵称、
标题、推荐批次或采集账号标识文章。图片来源键追加 detail/cover 与原始零基
索引；过滤掉某张图片后不能重排索引。文件内容仍以完整 SHA256 去重。
相同来源内容更新时保留新的内容哈希，不能凭来源键跳过质量升级。
"""
from __future__ import annotations

import base64
import html
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit


def _positive_number(value: object) -> str:
    text = str(value)
    if not text.isascii() or not text.isdecimal() or int(text) <= 0:
        raise ValueError("WeChat identity requires positive numeric IDs")
    return str(int(text))


@dataclass(frozen=True)
class WeChatArticleIdentity:
    bizuin: str
    mid: str
    idx: str

    def __post_init__(self):
        for key in ("bizuin", "mid", "idx"):
            object.__setattr__(self, key, _positive_number(getattr(self, key)))

    @classmethod
    def from_url(cls, url: str) -> "WeChatArticleIdentity":
        """只读取微信文章链接的稳定字段，忽略会话/分享/追踪参数。"""
        parsed = urlsplit(html.unescape(url))
        if parsed.hostname != "mp.weixin.qq.com" or parsed.path != "/s":
            raise ValueError("A canonical WeChat article URL is required")
        query = parse_qs(parsed.query)
        if any(len(query.get(key, [])) != 1 for key in ("__biz", "mid", "idx")):
            raise ValueError("Missing or ambiguous article identity")
        encoded = query["__biz"][0]
        try:
            bizuin = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True).decode("ascii")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid __biz") from exc
        return cls(bizuin, query["mid"][0], query["idx"][0])

    @property
    def author_id(self) -> str:
        return f"biz_{self.bizuin}"

    @property
    def article_id(self) -> str:
        return f"wechat:{self.bizuin}:{self.mid}:{self.idx}"

    def media_id(self, kind: str, index: int) -> str:
        if kind not in {"detail", "cover"} or isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("Invalid media role or original index")
        return f"{self.article_id}:{kind}:{index}"


def index_wechat_image_sources(objects: list[dict]) -> dict[str, list[dict]]:
    """将已解析的完整/字段边界明确的响应对象映射为 URL → 多条来源。

    只使用同一对象内的作者/文章字段，不用相邻标题猜归属。一个 URL 可被多个
    作者引用，保留所有来源，不将 URL 本身作为内容去重依据。
    """
    result: dict[str, list[dict]] = {}

    def add(identity, urls, kind, title="", nickname="", username=""):
        for index, item in enumerate(urls or []):
            url = item.get("cdn_url", "") if isinstance(item, dict) else item
            if not isinstance(url, str) or urlsplit(url).hostname != "mmbiz.qpic.cn":
                continue
            source = {"author_id": identity.author_id, "bizuin": identity.bizuin,
                      "mid": identity.mid, "idx": identity.idx, "article_id": identity.article_id,
                      "media_id": identity.media_id(kind, index), "kind": kind, "index": index,
                      "title": title, "author_name": nickname, "author_username": username}
            entries = result.setdefault(html.unescape(url), [])
            existing = next((row for row in entries if row["media_id"] == source["media_id"]), None)
            if existing is None:
                entries.append(source)
            else:
                for key, value in source.items():
                    if value and not existing.get(key):
                        existing[key] = value

    def visit(obj):
        if not isinstance(obj, dict):
            return
        if "picture_page_info_list" in obj and obj.get("link"):
            try:
                identity = WeChatArticleIdentity.from_url(obj["link"])
            except ValueError:
                pass
            else:
                add(identity, obj["picture_page_info_list"], "detail", obj.get("title", ""), obj.get("nick_name", ""), obj.get("user_name", ""))
        if obj.get("bizuin") and isinstance(obj.get("appmsg_info"), list):
            for article in obj["appmsg_info"]:
                try:
                    identity = WeChatArticleIdentity(obj["bizuin"], article.get("mid"), article.get("idx"))
                except ValueError:
                    continue
                add(identity, article.get("cover_pic_urls", []), "cover", article.get("title", ""))
        for child in obj.get("biz_info", []) if isinstance(obj.get("biz_info"), list) else []:
            visit(child)

    for obj in objects:
        visit(obj)
    return result
