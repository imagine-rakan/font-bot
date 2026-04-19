# -*- coding: utf-8 -*-
"""
preview.py — ميزة معاينة الخط
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
لا يحتوي على هاندلرات خاصة به — يعمل عبر PENDING_ADD في bot.py
الإعدادات تُحفظ في bot.db وتُعدَّل من لوحة التحكم
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import io
import sqlite3
import asyncio

from PIL import Image, ImageDraw, ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from bidi.algorithm import get_display
    BIDI_AVAILABLE = True
except ImportError:
    BIDI_AVAILABLE = False

try:
    import arabic_reshaper
    RESHAPER_AVAILABLE = True
except ImportError:
    RESHAPER_AVAILABLE = False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   الإعدادات الافتراضية
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DB = "bot.db"

_DEFAULTS = {
    "bg_color":            "15 15 15",
    "text_color":          "255 255 255",
    "watermark_color":     "130 130 130",
    "watermark_text":      "@YourBot",
    "font_size":           "80",
    "watermark_font_size": "24",
    "image_width":         "1200",
    "image_height":        "600",
}

PREVIEW_FIELD_LABELS = {
    "bg_color":            "لون الخلفية (RGB)",
    "text_color":          "لون النص (RGB)",
    "watermark_color":     "لون العلامة المائية (RGB)",
    "watermark_text":      "نص العلامة المائية",
    "font_size":           "حجم الخط",
    "watermark_font_size": "حجم خط العلامة المائية",
    "image_width":         "عرض الصورة (بكسل)",
    "image_height":        "ارتفاع الصورة (بكسل)",
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   قاعدة البيانات
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def init_preview_db(conn=None):
    """أنشئ الجدول وامله بالقيم الافتراضية — يُستدعى من init_db() في bot.py"""
    close = False
    if conn is None:
        conn = sqlite3.connect(DB)
        close = True
    conn.execute("""
        CREATE TABLE IF NOT EXISTS preview_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    for k, v in _DEFAULTS.items():
        conn.execute("INSERT OR IGNORE INTO preview_settings VALUES (?, ?)", (k, v))
    if close:
        conn.commit()
        conn.close()


def get_preview_config() -> dict:
    with sqlite3.connect(DB) as conn:
        rows = conn.execute("SELECT key, value FROM preview_settings").fetchall()
    cfg = dict(_DEFAULTS)
    cfg.update({k: v for k, v in rows})
    return cfg


def set_preview_config_field(field: str, raw_value: str):
    """يُرجع (True, '') أو (False, رسالة_خطأ)"""
    if field not in _DEFAULTS:
        return False, "حقل غير معروف"

    if field in ("bg_color", "text_color", "watermark_color"):
        parts = raw_value.strip().split()
        if len(parts) != 3:
            return False, "أرسل ثلاثة أرقام مفصولة بمسافة\nمثال: `255 255 255`"
        try:
            vals = [int(p) for p in parts]
            if not all(0 <= v <= 255 for v in vals):
                return False, "كل رقم يجب أن يكون بين 0 و 255"
        except ValueError:
            return False, "أرقام غير صحيحة"
        raw_value = " ".join(str(v) for v in vals)

    elif field in ("font_size", "watermark_font_size"):
        try:
            v = int(raw_value.strip())
            mn, mx = (20, 200) if field == "font_size" else (10, 60)
            if not (mn <= v <= mx):
                return False, f"الرقم يجب أن يكون بين {mn} و {mx}"
        except ValueError:
            return False, "أرسل رقماً صحيحاً"
        raw_value = str(v)

    elif field in ("image_width", "image_height"):
        try:
            v = int(raw_value.strip())
            if not (200 <= v <= 4000):
                return False, "القيمة يجب أن تكون بين 200 و 4000"
        except ValueError:
            return False, "أرسل رقماً صحيحاً"
        raw_value = str(v)

    elif field == "watermark_text":
        if len(raw_value) > 50:
            return False, "النص أطول من 50 حرفاً"

    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO preview_settings VALUES (?, ?)", (field, raw_value)
        )
        conn.commit()
    return True, ""


def reset_preview_config():
    with sqlite3.connect(DB) as conn:
        for k, v in _DEFAULTS.items():
            conn.execute(
                "INSERT OR REPLACE INTO preview_settings VALUES (?, ?)", (k, v)
            )
        conn.commit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   لوحة التحكم — قائمة الإعدادات
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _rgb_fmt(raw: str) -> str:
    parts = raw.split()
    return f"({', '.join(parts)})" if len(parts) == 3 else raw


def preview_settings_keyboard(cfg: dict):
    """يُرجع (نص_الرسالة, InlineKeyboardMarkup)"""
    text = (
        "🎨 *إعدادات المعاينة*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🖼 الخلفية:           `{_rgb_fmt(cfg['bg_color'])}`\n"
        f"✏️ النص:               `{_rgb_fmt(cfg['text_color'])}`\n"
        f"💧 العلامة المائية:   `{_rgb_fmt(cfg['watermark_color'])}`\n"
        f"📝 نص العلامة:        `{cfg['watermark_text']}`\n"
        f"🔤 حجم الخط:          `{cfg['font_size']}`\n"
        f"🔡 حجم خط العلامة:   `{cfg['watermark_font_size']}`\n"
        f"📐 العرض:             `{cfg['image_width']} px`\n"
        f"📏 الارتفاع:          `{cfg['image_height']} px`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "_اضغط على أي زر لتعديله_"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖼 لون الخلفية",        callback_data="preview_set|bg_color"),
            InlineKeyboardButton("✏️ لون النص",            callback_data="preview_set|text_color"),
        ],
        [
            InlineKeyboardButton("💧 لون العلامة المائية", callback_data="preview_set|watermark_color"),
            InlineKeyboardButton("📝 نص العلامة المائية",  callback_data="preview_set|watermark_text"),
        ],
        [
            InlineKeyboardButton("🔤 حجم الخط",            callback_data="preview_set|font_size"),
            InlineKeyboardButton("🔡 حجم خط العلامة",      callback_data="preview_set|watermark_font_size"),
        ],
        [
            InlineKeyboardButton("📐 عرض الصورة",          callback_data="preview_set|image_width"),
            InlineKeyboardButton("📏 ارتفاع الصورة",        callback_data="preview_set|image_height"),
        ],
        [InlineKeyboardButton("🔄 إعادة الإعدادات الافتراضية", callback_data="preview_reset")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="close")],
    ])
    return text, kb


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   توليد الصورة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_rgb(raw: str) -> tuple:
    parts = raw.strip().split()
    if len(parts) == 3:
        return tuple(int(p) for p in parts)
    return (255, 255, 255)


def _arabic(text: str) -> str:
    if RESHAPER_AVAILABLE:
        text = arabic_reshaper.reshape(text)
    if BIDI_AVAILABLE:
        text = get_display(text)
    return text


def _wrap(text: str, font, max_w: int) -> list:
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        words, cur = para.split(), ""
        for word in words:
            test = f"{cur} {word}".strip()
            if font.getbbox(test)[2] - font.getbbox(test)[0] <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
    return lines or [""]


def build_preview_image(text: str, font_path: str) -> io.BytesIO:
    cfg          = get_preview_config()
    W            = int(cfg["image_width"])
    H            = int(cfg["image_height"])
    PAD          = 70
    bg           = _parse_rgb(cfg["bg_color"])
    fg           = _parse_rgb(cfg["text_color"])
    wm_color     = _parse_rgb(cfg["watermark_color"])
    fsize        = int(cfg["font_size"])
    wm_fsize     = int(cfg["watermark_font_size"])
    wm_text      = cfg["watermark_text"]

    img  = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(font_path, fsize)
    except Exception:
        font = ImageFont.load_default()

    try:
        wm_font = ImageFont.truetype(font_path, wm_fsize)
    except Exception:
        wm_font = ImageFont.load_default()

    display = _arabic(text)
    lines   = _wrap(display, font, W - PAD * 2)
    gap     = 14
    heights = [font.getbbox(l or " ")[3] - font.getbbox(l or " ")[1] for l in lines]
    total_h = sum(heights) + gap * (len(lines) - 1)

    y = (H - total_h) // 2
    for i, line in enumerate(lines):
        if line:
            lw = font.getbbox(line)[2] - font.getbbox(line)[0]
            draw.text((W - PAD - lw, y), line, font=font, fill=fg)   # RTL
        y += heights[i] + gap

    wm_d = _arabic(wm_text)
    wm_h = wm_font.getbbox(wm_d)[3] - wm_font.getbbox(wm_d)[1]
    draw.text((PAD, H - PAD - wm_h), wm_d, font=wm_font, fill=wm_color)
    draw.line([(PAD, H - PAD + 6), (W - PAD, H - PAD + 6)], fill=wm_color, width=1)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


def cleanup_font(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
