"""Display desktop messages without changing their stored content or meaning.

The desktop provider sometimes serializes attachment metadata into a text
envelope. Only that exact envelope is separated; normal Markdown stays intact.
Previews come solely from image references present in the returned user message.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import ntpath
import re

ENVELOPE = re.compile(r'\A\s*# Files mentioned by the user:\s*\n(?P<files>.*?)^## My request:\s*\n(?P<body>.*)\Z', re.M | re.S)
FILE_HEADING = re.compile(r'^## (.+?):\s*([A-Za-z]:[/\\].*|/.*)$', re.M)


@lru_cache(maxsize=96)
def image_thumbnail(path: str, modified_ns: int, size: int) -> str | None:
    """Bounded, memory-only image preview; original files remain untouched."""
    from PIL import Image, ImageOps
    if size > 20 * 1024 * 1024:
        return None
    try:
        with Image.open(path) as image:
            if image.width * image.height > 40_000_000:
                return None
            image.thumbnail((640, 640))
            image = ImageOps.exif_transpose(image).convert('RGB')
            out = BytesIO()
            image.save(out, format='JPEG', quality=82)
        return 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode('ascii')
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def present_desktop_thread(payload: dict) -> dict:
    """Add displayText and attachments to user items from the public provider."""
    for turn in payload.get('turns', []):
        for item in turn.get('items', []):
            if item.get('type') != 'userMessage':
                continue
            content = item.get('content') or []
            text = item.get('text') or '\n'.join(part.get('text', '') for part in content if part.get('type') == 'text')
            match = ENVELOPE.match(text.replace('\r\n', '\n'))
            item['displayText'] = match['body'].strip('\n') if match else text
            references = []
            if match:
                references.extend((name, path.strip()) for name, path in FILE_HEADING.findall(match['files']))
            for part in content:
                if part.get('type') == 'localImage' and part.get('path'):
                    references.append((Path(part['path']).name, part['path']))
            attachments = []
            seen = set()
            for name, path in references[:12]:
                # Desktop envelopes and native parts may spell the same Windows
                # path with forward slashes or repeated escaped backslashes.
                identity = ntpath.normcase(ntpath.normpath(path))
                if identity in seen:
                    continue
                seen.add(identity)
                attachment = {'name': name, 'path': path, 'imageUrl': None}
                source = Path(path)
                if source.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}:
                    try:
                        stat = source.stat()
                        attachment['imageUrl'] = image_thumbnail(str(source), stat.st_mtime_ns, stat.st_size)
                    except OSError:
                        pass
                attachments.append(attachment)
            for part in content:
                if part.get('type') == 'image' and isinstance(part.get('url'), str) and part['url'].startswith('data:image/'):
                    attachments.append({'name': '图片', 'imageUrl': part['url']})
            item['attachments'] = attachments
    return payload
