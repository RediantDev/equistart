"""Фото без лишнего веса (тест 5.1: «очень долгая загрузка изображений»).

Снимок с телефона весит 3–8 МБ и 4000 px по ширине — на странице клуба таких несколько.
При загрузке фото уменьшается до разумного размера (по длинной стороне) и пережимается в JPEG;
для списков и миниатюр отдаётся уменьшенная копия, которая создаётся один раз и хранится рядом.
Без Pillow (например, в CI) всё работает как раньше — с исходными файлами.
"""
import io
import os

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None

THUMB_WIDTHS = (320, 640, 1200)


def _encode(im, keep_alpha: bool):
    buf = io.BytesIO()
    if keep_alpha:
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue(), "png"
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
    return buf.getvalue(), "jpg"


def optimize(f, max_side: int = 1600):
    """Уменьшить и пережать загруженную картинку. Возвращает файл для сохранения в FileField."""
    if Image is None:
        return f
    try:
        f.seek(0)
        im = Image.open(f)
        im = ImageOps.exif_transpose(im)  # фото с телефона «лежит на боку» без этого
        keep_alpha = im.mode in ("RGBA", "LA", "P") and "transparency" in im.info or im.mode in ("RGBA", "LA")
        im.thumbnail((max_side, max_side))
        data, ext = _encode(im, keep_alpha)
    except Exception:
        f.seek(0)
        return f
    if len(data) >= getattr(f, "size", len(data) + 1) and not getattr(f, "name", "").lower().endswith((".heic", ".heif")):
        f.seek(0)
        return f  # уже лёгкий файл — оставляем как есть
    base = os.path.splitext(os.path.basename(getattr(f, "name", "") or "photo"))[0][:60] or "photo"
    return ContentFile(data, name=f"{base}.{ext}")


def thumbnail_name(name: str, width: int) -> str:
    stem = os.path.splitext(name)[0]
    return f"thumbs/{width}/{stem}.jpg"


def thumbnail(name: str, width: int):
    """Путь к уменьшенной копии (создаётся при первом обращении) или None, если сделать нельзя."""
    if Image is None or width not in THUMB_WIDTHS:
        return None
    tname = thumbnail_name(name, width)
    if default_storage.exists(tname):
        return tname
    try:
        with default_storage.open(name, "rb") as src:
            im = Image.open(src)
            im = ImageOps.exif_transpose(im)
            if im.width <= width:
                return None
            im.thumbnail((width, width * 4))
            data, _ = _encode(im, keep_alpha=False)
        default_storage.save(tname, ContentFile(data))
        return tname
    except Exception:
        return None
