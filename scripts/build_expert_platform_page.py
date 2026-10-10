"""将专家平台研究页打包为后端原生静态页；数据唯一来源为frontend/public。

运行：uv run python scripts/build_expert_platform_page.py
生成：backend/static/expert-platforms.html（现有/static挂载直接提供，无需上传HTML附件）。
独立页面内嵌同一份JSON、样式与脚本，适用于单页发布及离线阅读；更新数据后重建。
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse


def update_existing_workbook(workbook: Path) -> None:
    """覆盖页面当前下载链接对应的文件，不新建附件或生成新URL。"""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root.parent / 'xlproject/src'))
    import xlproject.loadenv  # noqa: F401 -- 复用运行实例的数据目录配置
    from backend.core.resources.storage import get_attachments_dir

    dataset = json.loads((root / 'frontend/public/expert-platforms/data.json').read_text(encoding='utf-8'))
    url = urlparse(dataset['xlsx_url'])
    match = re.fullmatch(r'/static/attachments/([a-zA-Z0-9_-]+\.xlsx)', url.path)
    if url.netloc != 'code4101.com' or not match:
        raise ValueError('当前Excel链接不是CodeYun的xlsx附件链接')
    attachments = get_attachments_dir().resolve()
    target = (attachments / match.group(1)).resolve()
    if target.parent != attachments or not target.is_file():
        raise ValueError('固定链接对应的已有文件不存在')
    if not workbook.is_file() or workbook.suffix.lower() != '.xlsx':
        raise ValueError('请提供已生成的xlsx文件')
    shutil.copyfile(workbook, target)


def build_expert_platform_page() -> Path:
    root = Path(__file__).resolve().parents[1]
    source = root / "frontend/public/expert-platforms"
    html = (source / "index.html").read_text(encoding="utf-8")
    css = (source / "styles.css").read_text(encoding="utf-8")
    javascript = (source / "app.js").read_text(encoding="utf-8")
    data = (source / "data.json").read_text(encoding="utf-8").replace("<", "\\u003c")
    html = html.replace('<link rel="stylesheet" href="styles.css">', f"<style>{css}</style>")
    html = html.replace(
        '<script type="module" src="app.js"></script>',
        f'<script id="platform-data" type="application/json">{data}</script>'
        f'<script type="module">{javascript}</script>',
    )
    target = root / "backend/static/expert-platforms.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xlsx', type=Path, help='覆盖现有Excel链接对应文件；省略时只更新页面')
    args = parser.parse_args()
    if args.xlsx:
        update_existing_workbook(args.xlsx)
    print(build_expert_platform_page())
