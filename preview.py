# -*- coding: utf-8 -*-
"""
preview.py — ميزة معاينة الخط
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
طريقة الاستخدام:
  1. رُدّ على رسالة شخص بكلمة: معاينة
  2. أرسل النص المطلوب
  3. أرسل ملف الخط (.ttf / .otf)
  → يتم توليد صورة بالنص والخط مع دعم كامل للعربية وRTL

الإعدادات: تُعدَّل من لوحة التحكم زر 🎨 إعدادات المعاينة
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import io
import sqlite3
import asyncio

from PIL import Image, ImageDraw, ImageFont
from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
    CommandHandler,
)

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
#   قاعدة البيانات (نفس ملف bot.db)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DB = "bot.db"

# الإعدادات الافتراضية
_DEFAULTS = {
    "bg_color":             "15 15 15",
    "text_color":           "255 255 255",
    "watermark_color":      "130 130 130",
    "watermark_text":       "@YourBot",
    "font_size":            "80",
    "watermark_font_size":  "24",
    "image_width":          "1200",
    "image_height":         "600",
}

# أسماء الحقول بالعربي
PREVIEW_FIELD_LABELS = {
    "bg_color":             "لون الخلفية (RGB)",
    "text_color":           "لون النص (RGB)",
    "watermark_color":      "لون العلامة المائية (RGB)",
    "watermark_text":       "نص العلامة المائية",
    "font_size":            "حجم الخط",
    "watermark_font_size":  "حجم خط العلامة المائية",
    "image_width":          "عرض الصورة (بكسل)",
    "image_height":         "ارتفاع الصورة (بكسل)",
}

# حالات المحادثة
WAIT_TEXT, WAIT_FONT = range(2)

# مخزن مؤقت للنصوص
_pending_text: dict = {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   💾  دوال قاعدة البيانات
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ensure_table():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS preview_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        for k, v in _DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO preview_settings VALUES (?, ?)", (k, v)
            )
        conn.commit()


def get_preview_config() -> dict:
    _ensure_table()
    with sqlite3.connect(DB) as conn:
        rows = conn.execute("SELECT key, value FROM preview_settings").fetchall()
    cfg = dict(_DEFAULTS)
    cfg.update({k: v for k, v in rows})
    return cfg


def set_preview_config_field(field: str, raw_value: str):
    """
    تحديث حقل واحد.
    يُرجع (True, "") أو (False, رسالة_خطأ)
    """
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

    _ensure_table()
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO preview_settings VALUES (?, ?)",
            (field, raw_value)
        )
        conn.commit()
    return True, ""


def reset_preview_config():
    _ensure_table()
    with sqlite3.connect(DB) as conn:
        for k, v in _DEFAULTS.items():
            conn.execute(
                "INSERT OR REPLACE INTO preview_settings VALUES (?, ?)", (k, v)
            )
        conn.commit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   🎛  بناء قائمة الإعدادات
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _rgb_preview(raw: str) -> str:
    parts = raw.split()
    if len(parts) == 3:
        return f"({', '.join(parts)})"
    return raw


def preview_settings_keyboard(cfg: dict):
    """يُرجع (نص_الرسالة, لوحة_المفاتيح)"""
    text = (
        "🎨 *إعدادات المعاينة*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🖼 الخلفية:           `{_rgb_preview(cfg['bg_color'])}`\n"
        f"✏️ النص:               `{_rgb_preview(cfg['text_color'])}`\n"
        f"💧 العلامة المائية:   `{_rgb_preview(cfg['watermark_color'])}`\n"
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
        [InlineKeyboardButton("❌ إغلاق",                   callback_data="close")],
    ])
    return text, kb


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   🖼  توليد الصورة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_color(raw: str) -> tuple:
    parts = raw.strip().split()
    if len(parts) == 3:
        return tuple(int(p) for p in parts)
    return (255, 255, 255)


def _prepare_arabic(text: str) -> str:
    if RESHAPER_AVAILABLE:
        text = arabic_reshaper.reshape(text)
    if BIDI_AVAILABLE:
        text = get_display(text)
    return text


def _wrap_lines(text: str, font, max_w: int) -> list:
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        words, cur = para.split(), ""
        for word in words:
            test = f"{cur} {word}".strip()
            w = font.getbbox(test)[2] - font.getbbox(test)[0]
            if w <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
    return lines or [""]


def _build_image(text: str, font_path: str) -> io.BytesIO:
    cfg = get_preview_config()

    W            = int(cfg["image_width"])
    H            = int(cfg["image_height"])
    PAD          = 70
    bg_color     = _parse_color(cfg["bg_color"])
    text_color   = _parse_color(cfg["text_color"])
    wm_color     = _parse_color(cfg["watermark_color"])
    font_size    = int(cfg["font_size"])
    wm_font_size = int(cfg["watermark_font_size"])
    wm_text      = cfg["watermark_text"]

    img  = Image.new("RGB", (W, H), bg_color)
    draw = ImageDraw.Draw(img)

    try:
        main_font = ImageFont.truetype(font_path, font_size)
    except Exception:
        main_font = ImageFont.load_default()

    try:
        wm_font = ImageFont.truetype(font_path, wm_font_size)
    except Exception:
        wm_font = ImageFont.load_default()

    display = _prepare_arabic(text)
    lines   = _wrap_lines(display, main_font, W - PAD * 2)
    gap     = 14
    heights = [main_font.getbbox(l or " ")[3] - main_font.getbbox(l or " ")[1] for l in lines]
    total_h = sum(heights) + gap * (len(lines) - 1)

    y = (H - total_h) // 2
    for i, line in enumerate(lines):
        if line:
            lw = main_font.getbbox(line)[2] - main_font.getbbox(line)[0]
            x  = W - PAD - lw                   # RTL — محاذاة يمين
            draw.text((x, y), line, font=main_font, fill=text_color)
        y += heights[i] + gap

    # العلامة المائية
    wm_display = _prepare_arabic(wm_text)
    wm_b = wm_font.getbbox(wm_display)
    wm_h = wm_b[3] - wm_b[1]
    draw.text((PAD, H - PAD - wm_h), wm_display, font=wm_font, fill=wm_color)
    draw.line([(PAD, H - PAD + 6), (W - PAD, H - PAD + 6)], fill=wm_color, width=1)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


def _cleanup(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   🤖  هاندلرات المحادثة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.message
    if not msg or not msg.reply_to_message:
        return ConversationHandler.END

    await msg.reply_text(
        "✏️ أرسل النص الذي تريد معاينته:\n_(أرسل /cancel للإلغاء)_",
        parse_mode="Markdown",
    )
    return WAIT_TEXT


async def _receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text.strip()

    if len(text) > 200:
        text = text[:200]
        await update.message.reply_text("⚠️ تم اقتصار النص على 200 حرف.")

    _pending_text[uid] = text
    await update.message.reply_text(
        "🔤 أرسل الآن ملف الخط (.ttf أو .otf):\n_(أرسل /cancel للإلغاء)_",
        parse_mode="Markdown",
    )
    return WAIT_FONT


async def _receive_font(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    doc = update.message.document

    if not doc or not doc.file_name.lower().endswith((".ttf", ".otf")):
        await update.message.reply_text(
            "⚠️ الرجاء إرسال ملف خط بصيغة **.ttf** أو **.otf** فقط.",
            parse_mode="Markdown",
        )
        return WAIT_FONT

    preview_text = _pending_text.pop(uid, "نموذج نص عربي")
    processing   = await update.message.reply_text("⏳ جاري توليد المعاينة...")

    os.makedirs("temp_fonts", exist_ok=True)
    font_path = os.path.join("temp_fonts", f"{uid}_{doc.file_name}")

    try:
        file_obj = await context.bot.get_file(doc.file_id)
        await file_obj.download_to_drive(font_path)
    except Exception as e:
        await processing.edit_text(f"❌ فشل تنزيل الخط: {e}")
        return ConversationHandler.END

    try:
        loop      = asyncio.get_event_loop()
        image_buf = await loop.run_in_executor(None, _build_image, preview_text, font_path)
    except Exception as e:
        await processing.edit_text(f"❌ فشل توليد الصورة: {e}")
        _cleanup(font_path)
        return ConversationHandler.END

    cfg     = get_preview_config()
    caption = (
        f"🖼 *معاينة الخط:* `{doc.file_name}`\n"
        f"📝 *النص:* {preview_text[:80]}{'...' if len(preview_text) > 80 else ''}\n"
        f"🎨 خلفية: `{cfg['bg_color']}` | نص: `{cfg['text_color']}`"
    )

    try:
        await update.message.reply_photo(
            photo=image_buf, caption=caption, parse_mode="Markdown"
        )
        await processing.delete()
    except Exception as e:
        await processing.edit_text(f"❌ فشل إرسال الصورة: {e}")
    finally:
        _cleanup(font_path)

    return ConversationHandler.END


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _pending_text.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ تم إلغاء معاينة الخط.")
    return ConversationHandler.END


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   📌  دالة التسجيل
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def register_preview_handlers(app):
    """سجّل هاندلرات المعاينة — استدعِها من main() في bot.py."""
    _ensure_table()
    conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.TEXT & filters.REPLY & filters.Regex(r"^معاينة$"),
                _trigger,
            )
        ],
        states={
            WAIT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_text)],
            WAIT_FONT: [MessageHandler(filters.Document.ALL, _receive_font)],
        },
        fallbacks=[
            CommandHandler("cancel", _cancel),
            MessageHandler(filters.COMMAND, _cancel),
        ],
        per_user=True,
        per_chat=False,
        allow_reentry=True,
    )
    app.add_handler(conv)
