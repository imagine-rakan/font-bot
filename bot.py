# -*- coding: utf-8 -*-
"""
بوت ردود تلقائية عربي فقط
- يتعرف على المشرفين من المجموعة تلقائياً
- مالك البوت فقط من يستطيع حذف كل الردود
- يتجاهل الكلمات كـ "كلمة كاملة" (محددة بحدود) لحل مشكلة "الحروف" و"حروف"
- يرسل جميع الردود (نص+ملفات) مرتبة حسب ظهورها في الرسالة
- يحذف رسالة المشرف التي أرسل بها الرد تلقائياً
- الآن: الرد يعمل لأي عضو عند الرد على رسالة أخرى
- 🔍 بحث عن الردود مع عرض أزرار تعديل/حذف
- ✏️ تعديل الكلمة المفتاحية نفسها (زر منفصل)
"""
import hashlib
import logging
import os
import asyncio
import sqlite3
import json
import io
import random
import sys
import io as _io
import re
from dotenv import load_dotenv
from telegram.constants import ChatMemberStatus
from telegram import ChatMemberUpdated, ChatMember, Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    CallbackQueryHandler, MessageHandler, ChatMemberHandler, filters
)
import anti_designer
import preview as preview_module
PANEL_SESSIONS = {}   # user_id -> chat_id الذي جاء منه
# ==== إصلاح ترميز ويندوز ====
sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
load_dotenv()

# Telegram entity offsets/lengths are measured in UTF-16 code units, not Python code points.
def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2

TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))
DB = "bot.db"
ITEMS_PER_PAGE = 10
SEARCH_QUERIES = {}      # user_id -> آخر query
KW_TOKEN_MAP = {}        # (user_id, token) -> keyword

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()]
)


async def get_real_admins(chat_id, context):
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        return {m.user.id for m in admins}
    except:
        return set()


def init_db():
    with sqlite3.connect(DB) as conn:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS replies (
            keyword TEXT,
            data TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS ignores (
            word TEXT PRIMARY KEY
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
        """)

        # 🔴 جدول إعدادات البوت
        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # الحالة الافتراضية = تشغيل
        cur.execute(
            "INSERT OR IGNORE INTO settings VALUES ('bot_enabled', '1')"
        )
        # حذف رسالة المشرف بعد الرد
        cur.execute(
            "INSERT OR IGNORE INTO settings VALUES ('delete_admin_trigger', '1')"
        )


        # --- إضافة الجداول للميزة الجديدة ---
        anti_designer.init_db(conn)
        preview_module.init_preview_db(conn)

        conn.commit()
    
def is_bot_enabled() -> bool:
    with sqlite3.connect(DB) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='bot_enabled'"
        ).fetchone()
        return row is not None and row[0] == "1"
        
def is_delete_admin_trigger_enabled() -> bool:
    with sqlite3.connect(DB) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='delete_admin_trigger'"
        ).fetchone()
        return row and row[0] == "1"


def set_delete_admin_trigger(enabled: bool):
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "UPDATE settings SET value=? WHERE key='delete_admin_trigger'",
            ("1" if enabled else "0",)
        )
        conn.commit()
        


def set_bot_enabled(enabled: bool):
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "UPDATE settings SET value=? WHERE key='bot_enabled'",
            ("1" if enabled else "0",)
        )
        conn.commit()

    

def add_admin(user_id: int):
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT OR IGNORE INTO admins VALUES (?)", (user_id,))

def remove_admin(user_id: int):
    with sqlite3.connect(DB) as conn:
        conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))

def load_admins() -> set:
    with sqlite3.connect(DB) as conn:
        return {uid for uid, in conn.execute("SELECT user_id FROM admins")}

async def fetch_admins(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> set:
    admins = await context.bot.get_chat_administrators(chat_id)
    return {u.user.id for u in admins if u.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)}

async def is_admin_in_chat(user_id: int, chat_id: int, context):
    # مالك البوت دائمًا مشرف
    if user_id == OWNER_ID:
        return True

    try:
        member = await context.bot.get_chat_member(chat_id, user_id)

        return member.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR
        )

    except Exception:
        return False

# ==== كاش ====
REPLIES = {}
IGNORES = set()
PENDING_ADD = {}

LAST_PHOTO_ID = None

def load_cache():
    global REPLIES, IGNORES, ADMINS
    REPLIES, IGNORES, ADMINS = {}, set(), set()

    with sqlite3.connect(DB) as conn:
        cur = conn.cursor()

        # =========================
        # تحميل الردود (JSON)
        # =========================
        for kw, data in cur.execute("SELECT keyword, data FROM replies"):
            try:
                parsed = json.loads(data)
                REPLIES[kw] = parsed.get("replies", [])
            except Exception:
                # حماية لو وجد رد قديم جدًا
                REPLIES[kw] = [{
                    "text": data,
                    "entities": None,
                    "file_id": None
                }]

        # =========================
        # الكلمات المتجاهلة
        # =========================
        for w, in cur.execute("SELECT word FROM ignores"):
            IGNORES.add(w.lower())

        # =========================
        # المشرفين
        # =========================
        for uid, in cur.execute("SELECT user_id FROM admins"):
            ADMINS.add(uid)


def kw_token(user_id: int, kw: str) -> str:
    """يرجع token قصير ثابت للكلمة، ويخزّن الربط ليستعمله callback_router."""
    token = hashlib.md5(kw.encode("utf-8")).hexdigest()[:10]
    KW_TOKEN_MAP[(user_id, token)] = kw
    return token
# ======================
# ==== الأوامر الأساسية ====
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 أهلاً بك! استخدم /panel لفتح لوحة التحكم")

async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    # ❌ منع الفتح من الخاص
    if chat.type == "private":
        await update.message.reply_text(
            "❗ افتح لوحة التحكم من داخل القروب باستخدام /panel"
        )
        return

    # تحقق أنه مشرف
    if not await is_admin_in_chat(user.id, chat.id, context):
        await update.message.reply_text("🚫 غير مسموح لك.")
        return

    # اربط الجلسة بالقروب
    PANEL_SESSIONS[user.id] = chat.id

    # حاول الإرسال للخاص
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=f"🎛 لوحة التحكم\n\nالمصدر: {chat.title}",
            reply_markup=panel_keyboard()
        )

        # احذف الأمر من القروب
        try:
            await update.message.delete()
        except:
            pass

    except Exception:
        # ⬅️ هذا هو الحل
        await update.message.reply_text(
            "❗ لا أستطيع مراسلتك في الخاص\n"
            "👉 افتح الخاص مع البوت أولاً ثم أعد إرسال /panel"
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/panel → لوحة التحكم\n/stats → إحصائيات\n/help → هذه الرسالة")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_admin_in_chat(user_id, chat_id, context):
        await query.message.reply_text("🚫 غير مسموح لك.")
        return
    await update.message.reply_text(f"📊 الردود: {len(REPLIES)}\nالتجاهل: {len(IGNORES)}")

# ==== متابعة تغيير المشرفين ====
async def member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    event: ChatMemberUpdated = update.my_chat_member or update.chat_member
    if not event or event.chat.type not in ("group", "supergroup"):
        return
    old = event.old_chat_member
    new = event.new_chat_member
    if old.status != ChatMember.ADMINISTRATOR and new.status == ChatMember.ADMINISTRATOR:
        add_admin(new.user.id)
    elif old.status == ChatMember.ADMINISTRATOR and new.status != ChatMember.ADMINISTRATOR:
        remove_admin(new.user.id)

# ==== الكيبوردات ====
def panel_keyboard():
    enabled = is_bot_enabled()
    delete_enabled = is_delete_admin_trigger_enabled()

    toggle_bot_btn = InlineKeyboardButton(
        "⏸ إيقاف البوت" if enabled else "▶️ تشغيل البوت",
        callback_data="bot_off" if enabled else "bot_on"
    )

    delete_admin_btn = InlineKeyboardButton(
        "🗑 إيقاف حذف رسالة المشرف"
        if delete_enabled else
        "🗑 تشغيل حذف رسالة المشرف",
        callback_data="del_admin_off" if delete_enabled else "del_admin_on"
    )

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 منع ترويج الأسماء", callback_data="anti_designer_menu")],
        [InlineKeyboardButton("🎨 إعدادات المعاينة",  callback_data="preview_settings_menu")],
        [toggle_bot_btn],
        [delete_admin_btn],
        [InlineKeyboardButton("🔄 تحديث المشرفين", callback_data="refresh_admins")],
        [InlineKeyboardButton("👮‍♂️ عرض المشرفين", callback_data="show_admins")],
        [InlineKeyboardButton("➕ إضافة رد سريع", callback_data="add")],
        [InlineKeyboardButton("🔍 بحث عن رد", callback_data="search_reply")],
        [InlineKeyboardButton("📄 عرض الردود", callback_data="show_replies")],
        [InlineKeyboardButton("🚫 عرض الكلمات المتجاهلة", callback_data="show_ignores")],
        [InlineKeyboardButton("➕ إضافة كلمة تجاهل", callback_data="add_ignore")],
        [InlineKeyboardButton("🗑 حذف كلمة تجاهل", callback_data="del_ignore_manual")],
        [InlineKeyboardButton("🗑 حذف رد", callback_data="del")],
        [InlineKeyboardButton("🗑 حذف كل الردود", callback_data="del_all")],
        [InlineKeyboardButton("📤 تصدير JSON", callback_data="export")],
        [InlineKeyboardButton("📥 استيراد JSON", callback_data="import")],
        [InlineKeyboardButton("🔄 تحديث الكاش", callback_data="reload")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="close")]
    ])


# ==== دالة البحث عن الردود ====
async def search_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    query: str = None,
    page: int = 1
):
    if not query:
        PENDING_ADD[user_id] = {"state": "awaiting_search_query"}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
        await update.callback_query.message.reply_text(
            "🔍 أرسل كلمة أو جزء من الكلمة للبحث عن الردود:",
            reply_markup=kb
        )
        return

    # خزّن آخر بحث للمستخدم للتنقل بين الصفحات بدون وضع query داخل callback_data
    SEARCH_QUERIES[user_id] = query

    matched_items = []
    q = query.lower()
    for kw, items in REPLIES.items():
        if q in kw.lower():
            for item in items:
                matched_items.append((kw, item))

    if not matched_items:
        await update.callback_query.message.reply_text("❌ لم يتم العثور على ردود مطابقة للبحث.")
        return

    total = (len(matched_items) - 1) // ITEMS_PER_PAGE + 1
    start, end = (page - 1) * ITEMS_PER_PAGE, page * ITEMS_PER_PAGE

    buttons = []
    for kw, item in matched_items[start:end]:
        preview = (
            (item["text"][:30] + "...")
            if item.get("text") and len(item["text"]) > 30
            else (item.get("text") or "ملف فقط")
        )

        token = kw_token(user_id, kw)

        buttons.append([
            InlineKeyboardButton(f"📝 {kw} - {preview}", callback_data=f"view_reply|{token}")
        ])
        buttons.append([
            InlineKeyboardButton("✏️ الرد",   callback_data=f"edit_reply|{token}"),
            InlineKeyboardButton("🔡 الكلمة", callback_data=f"edit_keyword|{token}"),
            InlineKeyboardButton("🗑",        callback_data=f"del_reply|{token}")
        ])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"search_page|{page-1}"))
    if page < total:
        nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"search_page|{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("❌ إغلاق", callback_data="close")])

    await update.callback_query.message.reply_text(
        f"🔍 نتائج البحث عن: '{query}' (النتائج {len(matched_items)} - الصفحة {page}/{total})",
        reply_markup=InlineKeyboardMarkup(buttons)
    )



# ==== عرض تفاصيل الرد ====
async def view_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, keyword: str):
    items = REPLIES.get(keyword, [])
    if not items:
        await update.callback_query.message.reply_text("❌ الرد غير موجود.")
        return

    reply_text = f"📋 الردود المخزنة للكلمة: '{keyword}'\n\n"

    for i, item in enumerate(items, 1):
        reply_text += f"الرد {i}:\n"
        if item.get("text"):
            reply_text += f"النص: {item['text']}\n"
        if item.get("file_id"):
            reply_text += "📎 يحتوي على ملف\n"
        reply_text += "-" * 20 + "\n"

    user_id = update.effective_user.id
    token = kw_token(user_id, keyword)

    buttons = [
        [
            InlineKeyboardButton("✏️ الرد",   callback_data=f"edit_reply|{token}"),
            InlineKeyboardButton("🔡 الكلمة", callback_data=f"edit_keyword|{token}"),
            InlineKeyboardButton("🗑",        callback_data=f"del_reply|{token}")
        ],
        [InlineKeyboardButton("🔍 بحث جديد", callback_data="search_reply")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="close")]
    ]

    await update.callback_query.message.reply_text(
        reply_text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ==== عرض الردود المحدث ====
async def show_replies(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, page: int = 1):
    items = list(REPLIES.keys())
    if not items:
        await update.callback_query.message.reply_text("لا توجد ردود محفوظة")
        return

    total = (len(items) - 1) // ITEMS_PER_PAGE + 1
    start, end = (page - 1) * ITEMS_PER_PAGE, page * ITEMS_PER_PAGE

    buttons = []
    for kw in items[start:end]:
        token = kw_token(user_id, kw)

        buttons.append([
            InlineKeyboardButton(f"📝 {kw}", callback_data=f"view_reply|{token}")
        ])
        buttons.append([
            InlineKeyboardButton("✏️ الرد",   callback_data=f"edit_reply|{token}"),
            InlineKeyboardButton("🔡 الكلمة", callback_data=f"edit_keyword|{token}"),
            InlineKeyboardButton("🗑",        callback_data=f"del_reply|{token}")
        ])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page_replies|{page-1}"))
    if page < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page_replies|{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("❌ إغلاق", callback_data="close")])

    await update.callback_query.message.reply_text(
        "📄 الردود:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ==== عرض الكلمات المتجاهلة ====
async def show_ignores(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, page: int = 1):
    items = list(IGNORES)
    if not items:
        await update.callback_query.message.reply_text("لا توجد كلمات متجاهلة")
        return
    total = (len(items) - 1) // ITEMS_PER_PAGE + 1
    start, end = (page - 1) * ITEMS_PER_PAGE, page * ITEMS_PER_PAGE
    buttons = [[InlineKeyboardButton(f"🗑 {w}", callback_data=f"del_ignore|{w}")] for w in items[start:end]]
    nav = []
    if page < total: nav.append(InlineKeyboardButton("➡️", callback_data=f"page_ignores|{page+1}"))
    if nav: buttons.append(nav)
    buttons.append([InlineKeyboardButton("❌ إغلاق", callback_data="close")])
    await update.callback_query.message.reply_text("🚫 الكلمات المتجاهلة:", reply_markup=InlineKeyboardMarkup(buttons))

# ==== الرد التلقائي + حذف رسالة المشرف (محمي من file_id التالف) ====
async def auto_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_bot_enabled():
        return

    msg = update.message
    if not msg:
        return

    # النص أو الكابتشن
    content = (msg.text or msg.caption or "").lower()
    if not content:
        return

    # الرد على الرسالة الأصلية إن وُجدت
    reply_msg_id = (
        msg.reply_to_message.message_id
        if msg.reply_to_message
        else msg.message_id
    )

    # =========================
    # 🔍 البحث عن جميع الكلمات المطابقة مع مواقعها
    # =========================
    found_keywords = []  # قائمة من (موقع_في_الرسالة, كلمة_مفتاحية, رد)
    seen_signatures = set()  # لمنع تكرار نفس الرد
    
    for kw, replies in REPLIES.items():
        # دعم أكثر من كلمة لنفس الرد
        words = [w.strip().lower() for w in kw.split("|") if w.strip()]

        for word in words:
            if word in IGNORES:
                continue

            # مطابقة كلمة كاملة - نأخذ أول موقع فقط
            match = re.search(rf"\b{re.escape(word)}\b", content)
            
            if match:
                # اختيار رد عشوائي من الردود المتاحة
                r = random.choice(replies)
                
                # توقيع فريد لمنع التكرار
                signature = (r.get("text"), r.get("file_id"))
                
                if signature not in seen_signatures:
                    seen_signatures.add(signature)
                    position = match.start()
                    found_keywords.append((position, word, r))
                
                break  # لا نكرر نفس الرد لنفس الكلمة

    if not found_keywords:
        return

    # =========================
    # 📊 ترتيب الردود حسب موقعها في الرسالة
    # =========================
    found_keywords.sort(key=lambda x: x[0])


    # =========================
    # 📝 تجميع الردود (مع entities + إعادة حساب offsets)
    # =========================
    merged_chunks = []       # أجزاء النص + الفواصل
    merged_entities = []     # entities بعد تعديل offset
    files_to_send = []       # الملفات التي سيتم إرسالها منفصلة (مع الكلمة المفتاحية)
    cursor = 0               # طول النص الحالي

    for pos, word, r in found_keywords:
        text_content = r.get("text")
        file_id = r.get("file_id")

        # إذا كان هناك ملف، نحفظه مع الكلمة المفتاحية (ومع entities للـ caption إن وُجد)
        if file_id:
            files_to_send.append({
                "file_id": file_id,
                "keyword": word,
                "caption": r.get("text"),           # الـ caption الأصلي إن وُجد
                "entities": r.get("entities") or [] # ✅ لتنسيق الكابتشن لاحقاً
            })

        # إذا كان هناك نص، نضيفه للرسالة المدمجة مع نقل التنسيقات
        if not text_content:
            continue

        # فاصل بين الردود داخل الرسالة الواحدة
        if merged_chunks:
            sep = "\n\n"
            merged_chunks.append(sep)
            cursor += _utf16_len(sep)

        base = cursor
        merged_chunks.append(text_content)
        cursor += _utf16_len(text_content)

        # نقل entities مع تعديل offset
        raw_entities = r.get("entities") or []
        for e in raw_entities:
            ent = MessageEntity.de_json(e, context.bot)
            merged_entities.append(
                MessageEntity(
                    type=ent.type,
                    offset=ent.offset + base,   # ✅ تعديل offset بعد الدمج
                    length=ent.length,
                    url=getattr(ent, "url", None),
                    user=getattr(ent, "user", None),
                    language=getattr(ent, "language", None),
                    custom_emoji_id=getattr(ent, "custom_emoji_id", None),
                )
            )

    # =========================
    # 📤 إرسال الردود المدمجة (مع التنسيق)
    # =========================
    merged_text = "".join(merged_chunks)

    if merged_text:
        merged_entities.sort(key=lambda e: e.offset)
        await context.bot.send_message(
            chat_id=msg.chat.id,
            text=merged_text,
            entities=merged_entities if merged_entities else None,  # ✅ يدعم Bold/Italic/…/Link/Spoiler/Code/Quote
            reply_to_message_id=reply_msg_id,
            disable_web_page_preview=True
        )
    
    # إرسال الملفات مع caption يوضح الكلمة المفتاحية (مع الحفاظ على تنسيق caption الأصلي)
    for file_info in files_to_send:
        try:
            keyword = file_info["keyword"]
            original_caption = file_info.get("caption")
            raw_entities = file_info.get("entities") or []

            if original_caption:
                final_caption = f"{original_caption}\n\n🔑 الكلمة: {keyword}"
            else:
                final_caption = f"🔑 الكلمة: {keyword}"

            caption_entities = None
            if original_caption and raw_entities:
                # offsets تظل صحيحة لأننا أضفنا سطر الكلمة في النهاية
                caption_entities = [MessageEntity.de_json(e, context.bot) for e in raw_entities]

            await context.bot.send_document(
                chat_id=msg.chat.id,
                document=file_info["file_id"],
                caption=final_caption,
                caption_entities=caption_entities,
                reply_to_message_id=reply_msg_id
            )
        except Exception as e:
            logging.warning("File send failed: %s", e)
            
            
    # 🗑 حذف رسالة المشرف بعد الرد
    if is_delete_admin_trigger_enabled():
        try:
            if await is_admin_in_chat(
                msg.from_user.id,
                msg.chat.id,
                context
            ):
                await msg.delete()
        except Exception as e:
            logging.warning("Failed to delete admin message: %s", e)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message
    if not message:
        return

    # --- 1. فحص إعداد رسالة منع المصممين في الخاص ---
    if user_id in PENDING_ADD and PENDING_ADD[user_id].get("state") == "awaiting_anti_designer_msg":
        if update.effective_chat.type != "private":
            return
        await anti_designer.save_message(message, DB)
        PENDING_ADD.pop(user_id, None)
        await message.reply_text("✅ تم حفظ رسالة الحذف بنجاح!\n(تتضمن جميع التنسيقات والإيموجيات)")
        return
    # ------------------------------------------------



    # --- استقبال الكلمة الممنوعة الجديدة في الخاص ---
    if user_id in PENDING_ADD and PENDING_ADD[user_id].get("state") == "awaiting_anti_designer_kw":
        if update.effective_chat.type != "private" or not update.message.text:
            return
        
        new_kw = update.message.text.strip()
        anti_designer.add_keyword(DB, new_kw)
        PENDING_ADD.pop(user_id, None)
        await update.message.reply_text(f"✅ تم إضافة كلمة «{new_kw}» لقائمة المنع بنجاح.")
        return
    


    # (باقي الكود الخاص بك يظل كما هو، إلى أن نصل لفحص القروب)

    # 🔴 البوت متوقف
    if not is_bot_enabled():
        if not await is_admin_in_chat(user_id, message.chat.id, context):
            return

    # --- 2. هوك فحص الأسماء في القروبات (القلب النابض للميزة) ---
    if message.chat.type in ("group", "supergroup"):
        is_admin = await is_admin_in_chat(user_id, message.chat.id, context)
        # إذا رجعت الدالة بـ True، هذا يعني أنه تم مسح الرسالة، فنقوم بإنهاء الدالة
        if await anti_designer.process_message(update, context, is_admin, DB):
            return
    # -----------------------------------------------------------

    # =========================
    # 🎨 معاينة الخط — خطوة 1: اكتشاف كلمة "معاينة"
    # =========================
    if (
        message.text
        and message.text.strip() == "معاينة"
        and message.reply_to_message
        and message.chat.type in ("group", "supergroup")
    ):
        if await is_admin_in_chat(user_id, message.chat.id, context):
            PENDING_ADD[user_id] = {"state": "awaiting_preview_text"}
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
            await message.reply_text("✏️ أرسل النص الذي تريد معاينته:", reply_markup=kb)
            return

    # (باقي أكواد إضافة الردود والرد التلقائي كما هي تماماً)

    # =========================
    # 🔍 استقبال نص البحث
    # =========================
    if user_id in PENDING_ADD and PENDING_ADD[user_id].get("state") == "awaiting_search_query":
        if not message.text:
            await message.reply_text("❌ أرسل نصًا للبحث")
            return

        query_text = message.text.strip()
        PENDING_ADD.pop(user_id, None)

        class FakeQuery:
            def __init__(self, message):
                self.message = message
                self.answer = lambda: None

        fake_update = type("obj", (object,), {
            "callback_query": FakeQuery(message),
            "effective_user": update.effective_user
        })()

        await search_reply(fake_update, context, user_id, query_text)
        return

    # =========================
    # 🔡 تعديل الكلمة
    # =========================
    if user_id in PENDING_ADD and PENDING_ADD[user_id].get("state") == "awaiting_edit_keyword":
        new_kw = message.text.strip()
        old_kw = PENDING_ADD[user_id]["old_keyword"]

        with sqlite3.connect(DB) as conn:
            rows = list(conn.execute("SELECT data FROM replies WHERE keyword=?", (old_kw,)))
            conn.execute("DELETE FROM replies WHERE keyword=?", (old_kw,))
            for (data,) in rows:
                conn.execute("INSERT INTO replies VALUES (?, ?)", (new_kw, data))

        load_cache()
        PENDING_ADD.pop(user_id, None)
        await message.reply_text(f"✅ تم تغيير الكلمة من {old_kw} إلى {new_kw}")
        return

    # =========================
    # ✏️ تعديل رد
    # =========================
    if user_id in PENDING_ADD and PENDING_ADD[user_id].get("state") == "awaiting_edit_reply":
        kw = PENDING_ADD[user_id]["keyword"]

        reply_obj = {
            "text": message.text or message.caption,
            "entities": (
                [e.to_dict() for e in message.entities]
                if message.entities else
                [e.to_dict() for e in message.caption_entities]
                if message.caption_entities else None
            ),
            "file_id": (
                message.document.file_id
                if message.document else
                message.photo[-1].file_id
                if message.photo else None
            )
        }

        with sqlite3.connect(DB) as conn:
            conn.execute("DELETE FROM replies WHERE keyword=?", (kw,))
            conn.execute(
                "INSERT INTO replies VALUES (?, ?)",
                (kw, json.dumps({"replies": [reply_obj]}, ensure_ascii=False))
            )

        load_cache()
        PENDING_ADD.pop(user_id, None)
        await message.reply_text(f"✅ تم تعديل الرد: {kw}")
        return

    # =========================
    # ➕ إضافة رد جديد
    # =========================
    if user_id in PENDING_ADD:
        state = PENDING_ADD[user_id].get("state")

        if state == "awaiting_keyword":
            if not message.text:
                await message.reply_text("❌ أرسل الكلمات كنص")
                return

            PENDING_ADD[user_id]["keyword"] = message.text.strip()
            PENDING_ADD[user_id]["state"] = "awaiting_reply"
            await message.reply_text("📨 أرسل الرد (نص / صورة / ملف):")
            return

        if state == "awaiting_reply":
            kw = PENDING_ADD[user_id]["keyword"]

            reply_obj = {
                "text": message.text or message.caption,
                "entities": (
                    [e.to_dict() for e in message.entities]
                    if message.entities else
                    [e.to_dict() for e in message.caption_entities]
                    if message.caption_entities else None
                ),
                "file_id": (
                    message.document.file_id
                    if message.document else
                    message.photo[-1].file_id
                    if message.photo else None
                )
            }

            with sqlite3.connect(DB) as conn:
                conn.execute(
                    "INSERT INTO replies VALUES (?, ?)",
                    (kw, json.dumps({"replies": [reply_obj]}, ensure_ascii=False))
                )

            load_cache()
            PENDING_ADD.pop(user_id, None)
            await message.reply_text("✅ تم حفظ الرد")
            return
            
            
        # =========================
        # 🗑 حذف رد يدويًا
        # =========================
        if user_id in PENDING_ADD and PENDING_ADD[user_id].get("state") == "awaiting_del_keyword":
            kw = message.text.strip()
        
            with sqlite3.connect(DB) as conn:
                cur = conn.execute("DELETE FROM replies WHERE keyword=?", (kw,))
                if cur.rowcount == 0:
                    await message.reply_text("❌ لا يوجد رد بهذه الكلمة.")
                else:
                    await message.reply_text(f"🗑 تم حذف الرد: {kw}")
        
            load_cache()
            PENDING_ADD.pop(user_id, None)
            return   

        # =========================
        # 📥 استيراد JSON
        # =========================
        if user_id in PENDING_ADD and PENDING_ADD[user_id].get("state") == "awaiting_import_file":
            if not message.document:
                await message.reply_text("❌ أرسل ملف JSON فقط.")
                return
        
            if not message.document.file_name.endswith(".json"):
                await message.reply_text("❌ الملف يجب أن يكون بصيغة JSON.")
                return
        
            file = await message.document.get_file()
            data_bytes = await file.download_as_bytearray()
        
            try:
                data = json.loads(data_bytes.decode("utf-8"))
            except Exception as e:
                await message.reply_text("❌ فشل قراءة ملف JSON.")
                return
        
            with sqlite3.connect(DB) as conn:
                cur = conn.cursor()
        
                # حذف القديم
                cur.execute("DELETE FROM replies")
                cur.execute("DELETE FROM ignores")
        
                # استيراد الردود
                for kw, replies in data.get("replies", {}).items():
                    cur.execute(
                        "INSERT INTO replies VALUES (?, ?)",
                        (kw, json.dumps({"replies": replies}, ensure_ascii=False))
                    )
        
                # استيراد التجاهل
                for w in data.get("ignores", []):
                    cur.execute("INSERT OR IGNORE INTO ignores VALUES (?)", (w,))
        
                conn.commit()
        
            load_cache()
            PENDING_ADD.pop(user_id, None)
            await message.reply_text("✅ تم استيراد البيانات بنجاح.")
            return             

    # =========================
    # 🎨 معاينة الخط — خطوة 3: استقبال ملف الخط
    # =========================
    if user_id in PENDING_ADD and PENDING_ADD[user_id].get("state") == "awaiting_preview_font":
        if not message.document or not message.document.file_name.lower().endswith((".ttf", ".otf")):
            await message.reply_text("⚠️ أرسل ملف خط بصيغة **.ttf** أو **.otf** فقط.", parse_mode="Markdown")
            return

        preview_text = PENDING_ADD.pop(user_id)["text"]
        processing   = await message.reply_text("⏳ جاري توليد المعاينة...")

        os.makedirs("temp_fonts", exist_ok=True)
        font_path = os.path.join("temp_fonts", f"{user_id}_{message.document.file_name}")
        try:
            font_file = await message.document.get_file()
            await font_file.download_to_drive(font_path)
        except Exception as e:
            await processing.edit_text(f"❌ فشل تنزيل الخط: {e}")
            return

        try:
            loop      = asyncio.get_event_loop()
            image_buf = await loop.run_in_executor(None, preview_module.build_preview_image, preview_text, font_path)
        except Exception as e:
            await processing.edit_text(f"❌ فشل توليد الصورة: {e}")
            preview_module.cleanup_font(font_path)
            return

        cfg     = preview_module.get_preview_config()
        caption = (
            f"🖼 *معاينة الخط:* `{message.document.file_name}`\n"
            f"📝 *النص:* {preview_text[:80]}{'...' if len(preview_text) > 80 else ''}\n"
            f"🎨 خلفية: `{cfg['bg_color']}` | نص: `{cfg['text_color']}`"
        )
        try:
            await message.reply_photo(photo=image_buf, caption=caption, parse_mode="Markdown")
            await processing.delete()
        except Exception as e:
            await processing.edit_text(f"❌ فشل إرسال الصورة: {e}")
        finally:
            preview_module.cleanup_font(font_path)
        return

    # =========================
    # 🎨 معاينة الخط — خطوة 2: استقبال النص
    # =========================
    if user_id in PENDING_ADD and PENDING_ADD[user_id].get("state") == "awaiting_preview_text":
        if not message.text:
            await message.reply_text("❌ أرسل النص كرسالة نصية")
            return
        text = message.text.strip()
        if len(text) > 200:
            text = text[:200]
            await message.reply_text("⚠️ تم اقتصار النص على 200 حرف.")
        PENDING_ADD[user_id] = {"state": "awaiting_preview_font", "text": text}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
        await message.reply_text("🔤 الآن أرسل ملف الخط (.ttf أو .otf):", reply_markup=kb)
        return

    # =========================
    # 🎨 تعديل إعداد معاينة من لوحة التحكم
    # =========================
    if user_id in PENDING_ADD and PENDING_ADD[user_id].get("state") == "awaiting_preview_value":
        if not message.text:
            await message.reply_text("❌ أرسل القيمة كنص")
            return
        field = PENDING_ADD[user_id]["field"]
        ok, err = preview_module.set_preview_config_field(field, message.text.strip())
        if not ok:
            await message.reply_text(f"❌ قيمة غير صحيحة: {err}", parse_mode="Markdown")
            return
        PENDING_ADD.pop(user_id, None)
        cfg   = preview_module.get_preview_config()
        label = preview_module.PREVIEW_FIELD_LABELS.get(field, field)
        txt, kb = preview_module.preview_settings_keyboard(cfg)
        await message.reply_text(f"✅ تم تحديث *{label}*\n\n{txt}", reply_markup=kb, parse_mode="Markdown")
        return

    # =========================
    # 🤖 الرد التلقائي
    # =========================
    await auto_reply(update, context)



# ==== معالج الزرارات (محدّث) ====
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    # 🔐 حماية الجلسة
    if user_id not in PANEL_SESSIONS:
        await query.answer("❌ افتح اللوحة عبر /panel أولاً", show_alert=True)
        return

    source_chat = PANEL_SESSIONS[user_id]
    if update.effective_chat.type == "private" and source_chat > 0:
        await query.message.reply_text("❌ هذه اللوحة غير مرتبطة بقروب صالح")
        return

    # تحقق أنه ما زال مشرفًا
    if not await is_admin_in_chat(user_id, source_chat, context):
        PANEL_SESSIONS.pop(user_id, None)
        await query.answer("🚫 لم تعد مشرفًا", show_alert=True)
        try:
            await query.message.delete()
        except:
            pass
        return

    data = query.data

    data = query.data

    # ⏸ البوت متوقف
    if not is_bot_enabled() and data != "bot_on":
        await query.message.reply_text("⏸ البوت متوقف حالياً.")
        return

    # ===============================
    # التحكم بميزة منع المصممين (التعديل رقم 5)
    # ===============================
    if data == "panel_main":
        await query.message.edit_reply_markup(panel_keyboard())
        return

    if data == "anti_designer_menu":
        await query.message.edit_reply_markup(anti_designer.get_sub_menu(DB))
        return

    if data == "anti_designer_toggle":
        anti_designer.toggle(DB)
        await query.message.edit_reply_markup(anti_designer.get_sub_menu(DB))
        return

    if data == "anti_designer_set_msg":
        PENDING_ADD[user_id] = {"state": "awaiting_anti_designer_msg"}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
        text = (
            "💬 **تعيين رسالة الحذف للمصممين**\n\n"
            "أرسل الآن الرسالة (نص أو ملف مع نص).\n"
            "💡 يمكنك استخدام الخط **العريض**، الاقتباس، وأي إيموجي مميز براحتك.\n"
            "🔑 اكتب كلمة `اشارة` (أو إشارة) في أي مكان بالرسالة، وسيقوم البوت باستبدالها بمنشن للعضو!"
        )
        await query.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
        return
    



    # --- إضافة كلمة ممنوعة جديدة ---
    if data == "anti_designer_add_kw":
        PENDING_ADD[user_id] = {"state": "awaiting_anti_designer_kw"}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="anti_designer_menu")]])
        await query.message.reply_text("🚫 أرسل الكلمة التي تريد منع وجودها في أسماء الأعضاء:", reply_markup=kb)
        return

    # --- عرض الكلمات لحذفها ---
    if data == "anti_designer_show_kw":
        keywords = anti_designer.get_keywords(DB)
        if not keywords:
            await query.answer("لا توجد كلمات ممنوعة حالياً", show_alert=True)
            return
        
        buttons = []
        for kw in keywords:
            buttons.append([InlineKeyboardButton(f"🗑 {kw}", callback_data=f"anti_designer_del_kw|{kw}")])
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="anti_designer_menu")])
        
        await query.message.edit_text("إليك الكلمات الممنوعة حالياً، اضغط على الكلمة لحذفها:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # --- تنفيذ حذف الكلمة ---
    if data.startswith("anti_designer_del_kw|"):
        kw_to_del = data.split("|")[1]
        anti_designer.remove_keyword(DB, kw_to_del)
        await query.answer(f"تم حذف {kw_to_del}")
        await callback_router(update, context)
        return
    
    

    # =========================================================
    # Helpers: token -> keyword (مع توافق للأزرار القديمة)
    # =========================================================
    def _kw_from_token_or_kw(value: str) -> str:
        # إذا value هو token نرجع الكلمة من الخريطة، وإلا نعتبره kw قديم
        try:
            kw = KW_TOKEN_MAP.get((user_id, value))
            if kw:
                return kw
        except Exception:
            pass
        return value

    # ===============================
    # تشغيل / إيقاف البوت
    # ===============================
    if data == "bot_off":
        set_bot_enabled(False)
        await query.message.reply_text("⏸ تم إيقاف البوت.")
        await query.message.edit_reply_markup(panel_keyboard())
        return

    if data == "bot_on":
        set_bot_enabled(True)
        await query.message.reply_text("▶️ تم تشغيل البوت.")
        await query.message.edit_reply_markup(panel_keyboard())
        return

    # ===============================
    # حذف رسالة المشرف
    # ===============================
    if data == "del_admin_off":
        set_delete_admin_trigger(False)
        await query.message.reply_text("🗑 تم إيقاف حذف رسالة المشرف.")
        await query.message.edit_reply_markup(panel_keyboard())
        return

    if data == "del_admin_on":
        set_delete_admin_trigger(True)
        await query.message.reply_text("🗑 تم تشغيل حذف رسالة المشرف.")
        await query.message.edit_reply_markup(panel_keyboard())
        return

    # ===============================
    # 🔄 تحديث المشرفين
    # ===============================
    if data == "refresh_admins":
        try:
            admins = await context.bot.get_chat_administrators(source_chat)

            with sqlite3.connect(DB) as conn:
                conn.execute("DELETE FROM admins")
                for m in admins:
                    if m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                        conn.execute("INSERT OR IGNORE INTO admins VALUES (?)", (m.user.id,))
                conn.commit()

            load_cache()
            await query.message.reply_text(f"✅ تم تحديث المشرفين\n👥 العدد: {len(admins)}")
        except Exception as e:
            logging.error("Refresh admins failed: %s", e)
            await query.message.reply_text("❌ فشل تحديث المشرفين")
        return

    # ===============================
    # 👮‍♂️ عرض المشرفين
    # ===============================
    if data == "show_admins":
        try:
            admins = await context.bot.get_chat_administrators(source_chat)
            if not admins:
                await query.message.reply_text("❌ لا يوجد مشرفون في هذا القروب.")
                return

            owner = None
            moderators = []
            for m in admins:
                if m.status == ChatMemberStatus.OWNER:
                    owner = m
                elif m.status == ChatMemberStatus.ADMINISTRATOR:
                    moderators.append(m)

            text = "👮‍♂️ **مشرفو القروب**\n"
            text += f"📊 العدد: `{len(admins)}`\n\n"

            if owner:
                u = owner.user
                username = f"@{u.username}" if u.username else "—"
                text += (
                    "👑 **المالك**\n"
                    f"👤 {u.full_name}\n"
                    f"🔗 {username}\n"
                    f"🆔 `{u.id}`\n"
                    "━━━━━━━━━━━━━━\n\n"
                )

            if moderators:
                text += "🛡 **المشرفون**\n\n"
                for i, m in enumerate(moderators, 1):
                    u = m.user
                    username = f"@{u.username}" if u.username else "—"
                    text += (
                        f"{i}. 👤 {u.full_name}\n"
                        f"   🔗 {username}\n"
                        f"   🆔 `{u.id}`\n\n"
                    )

            await query.message.reply_text(text, parse_mode="Markdown")

        except Exception as e:
            logging.error("Show admins failed: %s", e)
            await query.message.reply_text(
                "❌ لا يمكن جلب المشرفين\n\n"
                "تأكد أن البوت:\n"
                "• مشرف في القروب\n"
                "• لديه صلاحية رؤية المشرفين"
            )
        return

    # ===============================
    # البحث
    # ===============================
    if data == "search_reply":
        await search_reply(update, context, user_id)
        return

    # ✅ الشكل الجديد: search_page|<page>  (بدون query داخل callback_data)
    if data.startswith("search_page|"):
        try:
            page = int(data.split("|", 1)[1])
        except:
            page = 1

        q = SEARCH_QUERIES.get(user_id)
        if not q:
            await query.message.reply_text("❌ انتهت جلسة البحث، اضغط 🔍 بحث عن رد من جديد.")
            return

        await search_reply(update, context, user_id, q, page)
        return

    # 🧯 توافق مع الأزرار القديمة إن بقيت (search_page|<query>|<page>)
    # هذا احتياط فقط لو ظهرت لك رسالة قديمة ما زالت فيها أزرار قديمة.
    if data.count("|") >= 2 and data.startswith("search_page|"):
        parts = data.split("|")
        if len(parts) >= 3:
            q = parts[1]
            try:
                page = int(parts[2])
            except:
                page = 1
            SEARCH_QUERIES[user_id] = q
            await search_reply(update, context, user_id, q, page)
        return

    # ===============================
    # عرض الرد
    # ===============================
    if data.startswith("view_reply|"):
        val = data.split("|", 1)[1]
        kw = _kw_from_token_or_kw(val)

        if kw not in REPLIES:
            await query.message.reply_text("❌ الرابط انتهت صلاحيته أو تم حذف الرد. ابحث من جديد.")
            return

        await view_reply(update, context, kw)
        return

    # ===============================
    # تعديل الكلمة
    # ===============================
    if data.startswith("edit_keyword|"):
        val = data.split("|", 1)[1]
        old_kw = _kw_from_token_or_kw(val)

        if old_kw not in REPLIES:
            await query.message.reply_text("❌ الرابط انتهت صلاحيته أو تم حذف الرد. ابحث من جديد.")
            return

        PENDING_ADD[user_id] = {"state": "awaiting_edit_keyword", "old_keyword": old_kw}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
        await query.message.reply_text(f"أرسل الكلمة الجديدة بدل «{old_kw}»:", reply_markup=kb)
        return

    # ===============================
    # إضافة رد
    # ===============================
    if data == "add":
        PENDING_ADD[user_id] = {"state": "awaiting_keyword", "chat_id": source_chat}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
        await query.message.reply_text(
            "✏️ أرسل الكلمات (افصل بينها بـ |)\nمثال:\nسلام|السلام|مرحبا",
            reply_markup=kb
        )
        return

    # ===============================
    # إلغاء
    # ===============================
    if data == "cancel_add":
        PENDING_ADD.pop(user_id, None)
        await query.message.reply_text("❌ تم إلغاء العملية.")
        return

    # ===============================
    # تحديث الكاش
    # ===============================
    if data == "reload":
        load_cache()
        await query.message.reply_text("🔄 تم تحديث الكاش.")
        return

    # ===============================
    # عرض الردود
    # ===============================
    if data == "show_replies":
        await show_replies(update, context, user_id)
        return

    if data.startswith("page_replies|"):
        try:
            page = int(data.split("|", 1)[1])
        except:
            page = 1
        await show_replies(update, context, user_id, page)
        return

    # ===============================
    # حذف رد مباشر
    # ===============================
    if data.startswith("del_reply|"):
        val = data.split("|", 1)[1]
        kw = _kw_from_token_or_kw(val)

        if kw not in REPLIES:
            await query.message.reply_text("❌ الرابط انتهت صلاحيته أو تم حذف الرد.")
            return

        with sqlite3.connect(DB) as conn:
            conn.execute("DELETE FROM replies WHERE keyword=?", (kw,))
        load_cache()
        await query.message.reply_text(f"🗑 تم حذف الرد: {kw}")
        return

    # ===============================
    # تعديل رد
    # ===============================
    if data.startswith("edit_reply|"):
        val = data.split("|", 1)[1]
        kw = _kw_from_token_or_kw(val)

        if kw not in REPLIES:
            await query.message.reply_text("❌ الرابط انتهت صلاحيته أو تم حذف الرد. ابحث من جديد.")
            return

        PENDING_ADD[user_id] = {"state": "awaiting_edit_reply", "keyword": kw}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
        await query.message.reply_text(f"✏️ أرسل الرد الجديد للكلمة: {kw}", reply_markup=kb)
        return

    # ===============================
    # التجاهل
    # ===============================
    if data == "show_ignores":
        await show_ignores(update, context, user_id)
        return

    if data.startswith("page_ignores|"):
        try:
            page = int(data.split("|", 1)[1])
        except:
            page = 1
        await show_ignores(update, context, user_id, page)
        return

    if data.startswith("del_ignore|"):
        word = data.split("|", 1)[1]
        with sqlite3.connect(DB) as conn:
            conn.execute("DELETE FROM ignores WHERE word=?", (word,))
        load_cache()
        await query.message.reply_text(f"🗑 تم حذف الكلمة المتجاهلة: {word}")
        return

    # ===============================
    # تصدير
    # ===============================
    if data == "export":
        data_exp = {"replies": REPLIES, "ignores": list(IGNORES)}
        bio = io.BytesIO(json.dumps(data_exp, ensure_ascii=False).encode("utf-8"))
        bio.name = "backup.json"
        await query.message.reply_document(bio, caption="📤 تم تصدير البيانات.")
        return

    # ===============================
    # استيراد
    # ===============================
    if data == "import":
        PENDING_ADD[user_id] = {"state": "awaiting_import_file"}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
        await query.message.reply_text("📥 أرسل ملف JSON للاستيراد:", reply_markup=kb)
        return

    # ===============================
    # إضافة تجاهل
    # ===============================
    if data == "add_ignore":
        PENDING_ADD[user_id] = {"state": "awaiting_ignore_word"}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
        await query.message.reply_text("أرسل الكلمة لإضافتها للتجاهل:", reply_markup=kb)
        return

    # ===============================
    # حذف تجاهل يدوي
    # ===============================
    if data == "del_ignore_manual":
        PENDING_ADD[user_id] = {"state": "awaiting_del_ignore"}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
        await query.message.reply_text("أرسل الكلمة المراد حذفها من التجاهل:", reply_markup=kb)
        return

    # ===============================
    # حذف رد يدوي
    # ===============================
    if data == "del":
        PENDING_ADD[user_id] = {"state": "awaiting_del_keyword"}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
        await query.message.reply_text("🗑 أرسل الكلمة التي تريد حذفها:", reply_markup=kb)
        return

    # ===============================
    # حذف الكل
    # ===============================
    if data == "del_all":
        if user_id != OWNER_ID:
            await query.message.reply_text("⚠️ هذا الأمر للمالك فقط.")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("تأكيد ✅", callback_data="confirm_del_all")],
            [InlineKeyboardButton("إلغاء ❌", callback_data="cancel_add")]
        ])
        await query.message.reply_text("⚠️ سيتم حذف جميع الردود، تأكيد؟", reply_markup=kb)
        return

    if data == "confirm_del_all":
        if user_id != OWNER_ID:
            await query.message.reply_text("⚠️ هذا الأمر للمالك فقط.")
            return
        with sqlite3.connect(DB) as conn:
            conn.execute("DELETE FROM replies")
        load_cache()
        await query.message.reply_text("✅ تم حذف جميع الردود.")
        return

    # ===============================
    # 🎨 قائمة إعدادات المعاينة
    # ===============================
    if data == "preview_settings_menu":
        cfg = preview_module.get_preview_config()
        txt, kb = preview_module.preview_settings_keyboard(cfg)
        await query.message.reply_text(txt, reply_markup=kb, parse_mode="Markdown")
        return

    if data.startswith("preview_set|"):
        field = data.split("|", 1)[1]
        label = preview_module.PREVIEW_FIELD_LABELS.get(field, field)
        hints = {
            "bg_color":            "مثال: `15 15 15` (أسود) أو `255 255 255` (أبيض)",
            "text_color":          "مثال: `255 255 255` (أبيض) أو `255 200 0` (ذهبي)",
            "watermark_color":     "مثال: `130 130 130` (رمادي)",
            "watermark_text":      "مثال: `@YourBot`",
            "font_size":           "رقم بين 20 و 200 — مثال: `80`",
            "watermark_font_size": "رقم بين 10 و 60 — مثال: `24`",
            "image_width":         "رقم بين 200 و 4000 — مثال: `1200`",
            "image_height":        "رقم بين 200 و 4000 — مثال: `600`",
        }
        PENDING_ADD[user_id] = {"state": "awaiting_preview_value", "field": field}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_add")]])
        await query.message.reply_text(
            f"✏️ *{label}*\n{hints.get(field, '')}\n_أرسل القيمة الجديدة:_",
            reply_markup=kb, parse_mode="Markdown"
        )
        return

    if data == "preview_reset":
        preview_module.reset_preview_config()
        cfg = preview_module.get_preview_config()
        txt, kb = preview_module.preview_settings_keyboard(cfg)
        await query.message.reply_text("✅ تم إعادة الإعدادات الافتراضية\n\n" + txt, reply_markup=kb, parse_mode="Markdown")
        return

    # ===============================
    # إغلاق
    # ===============================
    if data == "close":
        try:
            await query.message.delete()
        except:
            pass
        return
# ==== main ====
def main():
    init_db()
    load_cache()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(ChatMemberHandler(member_update, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.ALL, message_handler))
    logging.info("--> Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
