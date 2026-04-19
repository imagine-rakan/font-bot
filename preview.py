"""
preview.py — ميزة معاينة الخط
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
طريقة الاستخدام:
  1. رُدّ على رسالة شخص بكلمة: معاينة
  2. أرسل النص المطلوب
  3. أرسل ملف الخط (.ttf / .otf)
  → سيتم توليد صورة بالنص والخط مع دعم كامل للعربية وRTL

الإعدادات الافتراضية (قابلة للتعديل في CONFIG أدناه):
  - لون النص       : أبيض
  - لون الخلفية    : أسود
  - العلامة المائية: @YourBot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import io
import asyncio
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters
from pyrogram.types import Message

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
    # أبعاد الصورة
    "image_width": 1200,
    "image_height": 600,

    # الألوان (R, G, B) أو اسم اللون
    "bg_color": (15, 15, 15),          # لون الخلفية
    "text_color": (255, 255, 255),      # لون النص الرئيسي
    "watermark_color": (120, 120, 120), # لون العلامة المائية

    # حجم الخط
    "font_size": 72,
    "watermark_font_size": 22,

    # العلامة المائية (غيّر النص هنا)
    "watermark_text": "@YourBot",

    # الحشو (padding) من الحواف
    "padding": 60,

    # الحد الأقصى لعدد أحرف النص المعروض
    "max_text_length": 200,

    # مسار خط احتياطي للعلامة المائية (فارغ = خط النظام)
    "fallback_font_path": "",

    # مجلد مؤقت لتخزين ملفات الخطوط
    "temp_dir": "temp_fonts",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#     حالات المحادثة (State)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# pending_preview[user_id] = {"step": "text"|"font", "text": str, "replied_msg": Message}
pending_preview: dict = {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#        🔧  دوال مساعدة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def prepare_arabic_text(text: str) -> str:
    """إعادة تشكيل النص العربي ودعم RTL."""
    if RESHAPER_AVAILABLE:
        text = arabic_reshaper.reshape(text)
    if BIDI_AVAILABLE:
        text = get_display(text)
    return text


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """تقسيم النص إلى أسطر بحيث لا يتجاوز عرض الصورة."""
    lines = []
    # تقسيم أولي على سطور المستخدم
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        words = paragraph.split()
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip()
            bbox = font.getbbox(test_line)
            w = bbox[2] - bbox[0]
            if w <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
    return lines


def generate_preview_image(
    text: str,
    font_path: str,
    cfg: dict = CONFIG,
) -> io.BytesIO:
    """توليد صورة المعاينة وإرجاعها كـ BytesIO."""

    W = cfg["image_width"]
    H = cfg["image_height"]
    padding = cfg["padding"]

    # --- إنشاء الصورة ---
    img = Image.new("RGB", (W, H), color=cfg["bg_color"])
    draw = ImageDraw.Draw(img)

    # --- تحميل الخط ---
    try:
        font = ImageFont.truetype(font_path, cfg["font_size"])
    except Exception:
        font = ImageFont.load_default()

    # --- تحميل خط العلامة المائية ---
    wm_font_path = cfg.get("fallback_font_path") or font_path
    try:
        wm_font = ImageFont.truetype(wm_font_path, cfg["watermark_font_size"])
    except Exception:
        wm_font = ImageFont.load_default()

    # --- معالجة النص العربي ---
    display_text = prepare_arabic_text(text)

    # --- تقسيم النص إلى أسطر ---
    max_text_width = W - padding * 2
    lines = wrap_text(display_text, font, max_text_width)

    # --- حساب الارتفاع الكلي للنص ---
    line_spacing = 10
    line_heights = []
    for line in lines:
        bbox = font.getbbox(line if line else " ")
        line_heights.append(bbox[3] - bbox[1])
    total_text_height = sum(line_heights) + line_spacing * (len(lines) - 1)

    # --- رسم النص في المنتصف (RTL) ---
    y = (H - total_text_height) // 2
    for i, line in enumerate(lines):
        if not line:
            y += line_heights[i] + line_spacing
            continue
        bbox = font.getbbox(line)
        line_w = bbox[2] - bbox[0]
        # محاذاة يمين لـ RTL
        x = W - padding - line_w
        draw.text((x, y), line, font=font, fill=cfg["text_color"])
        y += line_heights[i] + line_spacing

    # --- رسم العلامة المائية (أسفل اليسار) ---
    wm_text = cfg["watermark_text"]
    wm_bbox = wm_font.getbbox(wm_text)
    wm_w = wm_bbox[2] - wm_bbox[0]
    wm_h = wm_bbox[3] - wm_bbox[1]
    wm_x = padding
    wm_y = H - padding - wm_h
    draw.text((wm_x, wm_y), wm_text, font=wm_font, fill=cfg["watermark_color"])

    # --- خط فاصل رفيع تحت العلامة المائية ---
    draw.line(
        [(padding, H - padding + 4), (W - padding, H - padding + 4)],
        fill=cfg["watermark_color"],
        width=1,
    )

    # --- تصدير كـ BytesIO ---
    output = io.BytesIO()
    img.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#        🤖  هاندلرات البوت
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def register_preview_handlers(app: Client):
    """
    تسجيل جميع هاندلرات ميزة المعاينة.
    استدعِ هذه الدالة من الملف الرئيسي (main.py / bot.py).

    مثال:
        from preview import register_preview_handlers
        register_preview_handlers(app)
    """

    os.makedirs(CONFIG["temp_dir"], exist_ok=True)

    # ── الخطوة 0: تفعيل الميزة بكلمة "معاينة" ──────────────────────────
    @app.on_message(
        filters.text
        & filters.reply
        & filters.create(lambda _, __, m: m.text and m.text.strip() == "معاينة")
    )
    async def cmd_preview(client: Client, message: Message):
        user_id = message.from_user.id

        # تجاهل أي جلسة سابقة
        pending_preview[user_id] = {
            "step": "text",
            "replied_msg": message.reply_to_message,
            "text": None,
        }

        await message.reply_text(
            "✏️ **أرسل النص الذي تريد معاينته:**\n"
            "_(يمكنك إلغاء العملية بإرسال /cancel)_",
            quote=True,
        )

    # ── الخطوة 1: استقبال النص ──────────────────────────────────────────
    @app.on_message(
        filters.text
        & filters.create(
            lambda _, __, m: (
                m.from_user
                and m.from_user.id in pending_preview
                and pending_preview[m.from_user.id].get("step") == "text"
            )
        )
    )
    async def receive_text(client: Client, message: Message):
        user_id = message.from_user.id
        text = message.text.strip()

        # أمر الإلغاء
        if text.lower() in ("/cancel", "إلغاء"):
            pending_preview.pop(user_id, None)
            await message.reply_text("❌ تم إلغاء عملية المعاينة.")
            return

        # تقليص النص إذا تجاوز الحد
        if len(text) > CONFIG["max_text_length"]:
            text = text[: CONFIG["max_text_length"]]
            await message.reply_text(
                f"⚠️ النص طويل جداً، سيتم اقتصاره على {CONFIG['max_text_length']} حرف."
            )

        pending_preview[user_id]["text"] = text
        pending_preview[user_id]["step"] = "font"

        await message.reply_text(
            "🔤 **الآن أرسل ملف الخط** (.ttf أو .otf):\n"
            "_(أو أرسل /cancel للإلغاء)_",
            quote=True,
        )

    # ── الخطوة 2: استقبال ملف الخط وتوليد الصورة ───────────────────────
    @app.on_message(
        filters.document
        & filters.create(
            lambda _, __, m: (
                m.from_user
                and m.from_user.id in pending_preview
                and pending_preview[m.from_user.id].get("step") == "font"
            )
        )
    )
    async def receive_font(client: Client, message: Message):
        user_id = message.from_user.id
        doc = message.document

        # التحقق من امتداد الملف
        if not doc.file_name or not doc.file_name.lower().endswith((".ttf", ".otf")):
            await message.reply_text(
                "⚠️ الرجاء إرسال ملف خط بصيغة **.ttf** أو **.otf** فقط."
            )
            return

        state = pending_preview.pop(user_id, {})
        preview_text = state.get("text", "نموذج نص عربي")

        processing_msg = await message.reply_text("⏳ جاري توليد المعاينة...")

        # تنزيل الخط مؤقتاً
        font_path = os.path.join(CONFIG["temp_dir"], f"{user_id}_{doc.file_name}")
        try:
            await client.download_media(message, file_name=font_path)
        except Exception as e:
            await processing_msg.edit_text(f"❌ فشل تنزيل الخط: {e}")
            return

        # توليد الصورة
        try:
            image_bytes = await asyncio.get_event_loop().run_in_executor(
                None,
                generate_preview_image,
                preview_text,
                font_path,
                CONFIG,
            )
        except Exception as e:
            await processing_msg.edit_text(f"❌ فشل توليد الصورة: {e}")
            _cleanup(font_path)
            return

        # إرسال الصورة
        caption = (
            f"🖼 **معاينة الخط:** `{doc.file_name}`\n"
            f"📝 **النص:** {preview_text[:80]}{'...' if len(preview_text) > 80 else ''}"
        )

        try:
            await message.reply_photo(
                photo=image_bytes,
                caption=caption,
                quote=True,
            )
            await processing_msg.delete()
        except Exception as e:
            await processing_msg.edit_text(f"❌ فشل إرسال الصورة: {e}")
        finally:
            _cleanup(font_path)

    # ── معالجة /cancel في أي وقت ────────────────────────────────────────
    @app.on_message(
        filters.command("cancel")
        & filters.create(lambda _, __, m: m.from_user and m.from_user.id in pending_preview)
    )
    async def cancel_preview(client: Client, message: Message):
        pending_preview.pop(message.from_user.id, None)
        await message.reply_text("❌ تم إلغاء عملية المعاينة.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#        🛠  دوال داخلية
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _cleanup(path: str):
    """حذف الملف المؤقت."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
