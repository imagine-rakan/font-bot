# -*- coding: utf-8 -*-
"""
preview.py — ميزة معاينة الخط
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
طريقة الاستخدام:
  1. رُدّ على رسالة شخص بكلمة: معاينة
  2. أرسل النص المطلوب
  3. أرسل ملف الخط (.ttf / .otf)
  → يتم توليد صورة بالنص والخط مع دعم كامل للعربية وRTL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import io
import asyncio

from PIL import Image, ImageDraw, ImageFont
from telegram import Update, Message
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
#        ⚙️  الإعدادات
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONFIG = {
    "image_width":          1200,
    "image_height":         600,
    "bg_color":             (15, 15, 15),           # لون الخلفية
    "text_color":           (255, 255, 255),         # لون النص
    "watermark_color":      (130, 130, 130),         # لون العلامة المائية
    "font_size":            80,
    "watermark_font_size":  24,
    "watermark_text":       "@YourBot",              # ← غيّر اسم البوت هنا
    "padding":              70,
    "max_text_length":      200,
    "temp_dir":             "temp_fonts",
}

# حالات المحادثة
WAIT_TEXT, WAIT_FONT = range(2)

# مخزن مؤقت: user_id -> نص المعاينة
_pending_text: dict[int, str] = {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#        🔧  دوال مساعدة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _prepare_arabic(text: str) -> str:
    """إعادة تشكيل النص العربي ودعم RTL."""
    if RESHAPER_AVAILABLE:
        text = arabic_reshaper.reshape(text)
    if BIDI_AVAILABLE:
        text = get_display(text)
    return text


def _wrap_lines(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """تقسيم النص إلى أسطر لا تتجاوز max_w بكسل."""
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
    """توليد صورة المعاينة وإرجاعها كـ BytesIO."""
    cfg = CONFIG
    W, H, PAD = cfg["image_width"], cfg["image_height"], cfg["padding"]

    img  = Image.new("RGB", (W, H), cfg["bg_color"])
    draw = ImageDraw.Draw(img)

    # ── تحميل الخطوط ──
    try:
        main_font = ImageFont.truetype(font_path, cfg["font_size"])
    except Exception:
        main_font = ImageFont.load_default()

    try:
        wm_font = ImageFont.truetype(font_path, cfg["watermark_font_size"])
    except Exception:
        wm_font = ImageFont.load_default()

    # ── تجهيز النص ──
    display = _prepare_arabic(text)
    lines   = _wrap_lines(display, main_font, W - PAD * 2)

    # ── حساب الارتفاع الكلي ──
    gap = 12
    heights = [main_font.getbbox(l or " ")[3] - main_font.getbbox(l or " ")[1] for l in lines]
    total_h = sum(heights) + gap * (len(lines) - 1)

    # ── رسم النص (محاذاة يمين - RTL) ──
    y = (H - total_h) // 2
    for i, line in enumerate(lines):
        if line:
            lw = main_font.getbbox(line)[2] - main_font.getbbox(line)[0]
            x  = W - PAD - lw                          # محاذاة يمين
            draw.text((x, y), line, font=main_font, fill=cfg["text_color"])
        y += heights[i] + gap

    # ── العلامة المائية (أسفل يسار) ──
    wm   = _prepare_arabic(cfg["watermark_text"])
    wm_b = wm_font.getbbox(wm)
    wm_h = wm_b[3] - wm_b[1]
    draw.text((PAD, H - PAD - wm_h), wm, font=wm_font, fill=cfg["watermark_color"])

    # ── خط فاصل رفيع ──
    draw.line([(PAD, H - PAD + 6), (W - PAD, H - PAD + 6)],
              fill=cfg["watermark_color"], width=1)

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
#     🤖  هاندلرات المحادثة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخطوة 0 — اكتشاف كلمة 'معاينة' في رد على رسالة."""
    msg: Message = update.message
    if not msg or not msg.reply_to_message:
        return ConversationHandler.END

    await msg.reply_text(
        "✏️ أرسل النص الذي تريد معاينته:\n_(أرسل /cancel للإلغاء)_",
        parse_mode="Markdown",
    )
    return WAIT_TEXT


async def _receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخطوة 1 — استقبال النص."""
    uid  = update.effective_user.id
    text = update.message.text.strip()

    if len(text) > CONFIG["max_text_length"]:
        text = text[:CONFIG["max_text_length"]]
        await update.message.reply_text(
            f"⚠️ تم اقتصار النص على {CONFIG['max_text_length']} حرف."
        )

    _pending_text[uid] = text
    await update.message.reply_text(
        "🔤 أرسل الآن ملف الخط (.ttf أو .otf):\n_(أرسل /cancel للإلغاء)_",
        parse_mode="Markdown",
    )
    return WAIT_FONT


async def _receive_font(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الخطوة 2 — استقبال ملف الخط وتوليد الصورة."""
    uid = update.effective_user.id
    doc = update.message.document

    if not doc or not doc.file_name.lower().endswith((".ttf", ".otf")):
        await update.message.reply_text(
            "⚠️ الرجاء إرسال ملف خط بصيغة **.ttf** أو **.otf** فقط.",
            parse_mode="Markdown",
        )
        return WAIT_FONT          # نبقى في نفس الخطوة

    preview_text = _pending_text.pop(uid, "نموذج نص عربي")
    processing   = await update.message.reply_text("⏳ جاري توليد المعاينة...")

    # تنزيل الخط
    os.makedirs(CONFIG["temp_dir"], exist_ok=True)
    font_path = os.path.join(CONFIG["temp_dir"], f"{uid}_{doc.file_name}")
    try:
        file_obj = await context.bot.get_file(doc.file_id)
        await file_obj.download_to_drive(font_path)
    except Exception as e:
        await processing.edit_text(f"❌ فشل تنزيل الخط: {e}")
        return ConversationHandler.END

    # توليد الصورة في thread منفصل (لا يعطّل البوت)
    try:
        loop      = asyncio.get_event_loop()
        image_buf = await loop.run_in_executor(None, _build_image, preview_text, font_path)
    except Exception as e:
        await processing.edit_text(f"❌ فشل توليد الصورة: {e}")
        _cleanup(font_path)
        return ConversationHandler.END

    caption = (
        f"🖼 **معاينة الخط:** `{doc.file_name}`\n"
        f"📝 **النص:** {preview_text[:80]}{'...' if len(preview_text) > 80 else ''}"
    )

    try:
        await update.message.reply_photo(photo=image_buf, caption=caption, parse_mode="Markdown")
        await processing.delete()
    except Exception as e:
        await processing.edit_text(f"❌ فشل إرسال الصورة: {e}")
    finally:
        _cleanup(font_path)

    return ConversationHandler.END


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء العملية في أي خطوة."""
    _pending_text.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ تم إلغاء معاينة الخط.")
    return ConversationHandler.END


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   📌  دالة التسجيل (تُستدعى من bot.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def register_preview_handlers(app):
    """
    سجّل هاندلرات المعاينة في كائن Application.
    استدعِها قبل app.run_polling() في main().
    """
    conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.TEXT & filters.REPLY &
                filters.Regex(r"^معاينة$"),
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
        per_chat=False,          # يعمل في الخاص والمجموعات
        allow_reentry=True,
    )
    app.add_handler(conv)
