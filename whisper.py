# -*- coding: utf-8 -*-
import sqlite3
import uuid
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ContextTypes

DB = "bot.db"

# 1. تحديث قاعدة البيانات لدعم استقبال الهمسة عبر "اليوزر"
def init_whisper_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS whispers (
                id TEXT PRIMARY KEY,
                sender_id INTEGER,
                receiver_id INTEGER,
                receiver_username TEXT,
                text TEXT
            )
        """)
        # ترقية الجدول القديم لو موجود (لإضافة عمود اليوزر)
        try:
            conn.execute("ALTER TABLE whispers ADD COLUMN receiver_username TEXT")
        except:
            pass
        conn.commit()

init_whisper_db()

# 2. الطريقة الأولى: الهمسة عبر الرد (القديمة)
async def handle_whisper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text or not message.reply_to_message:
        return

    text = message.text.strip()
    if text.startswith("همسة "):
        whisper_text = text.replace("همسة ", "", 1).strip()
        if not whisper_text:
            return

        sender = message.from_user
        receiver = message.reply_to_message.from_user

        if sender.id == receiver.id:
            await message.reply_text("❌ لا يمكنك الهمس لنفسك!")
            return
        if receiver.is_bot:
            await message.reply_text("❌ لا يمكنك الهمس للبوتات!")
            return

        whisper_id = str(uuid.uuid4())[:8]
        with sqlite3.connect(DB) as conn:
            conn.execute("INSERT INTO whispers (id, sender_id, receiver_id, receiver_username, text) VALUES (?, ?, ?, ?, ?)",
                         (whisper_id, sender.id, receiver.id, None, whisper_text))
            conn.commit()

        try:
            await message.delete()
        except Exception as e:
            logging.warning(f"لم أتمكن من حذف رسالة الهمسة: {e}")

        # الهوية الخاصة
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔐 افتح الهمسة", callback_data=f"wsp|{whisper_id}")]])
        text_msg = f"💌 **همسة مغلفة**\nمن: [{sender.first_name}](tg://user?id={sender.id})\nإلى: [{receiver.first_name}](tg://user?id={receiver.id})"
        
        await context.bot.send_message(
            chat_id=message.chat_id, 
            text=text_msg, 
            reply_markup=kb, 
            parse_mode="Markdown"
        )

# 3. الطريقة الثانية: الهمسة عبر الانلاين (الميزة الجديدة)
async def inline_whisper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query:
        return

    # استخراج الرسالة واليوزر: (الكلام ثم مسافة ثم @يوزر)
    match = re.search(r'^(.*?)\s+@(\w+)$', query)
    if not match:
        # رسالة توجيهية بتظهر لو المستخدم لسه بيكتب ومحطش منشن
        results = [
            InlineQueryResultArticle(
                id="help",
                title="⚠️ طريقة الهمسة السريعة",
                description="اكتب رسالتك ثم مسافة ثم @يوزر_الشخص",
                input_message_content=InputTextMessageContent("💡 **طريقة الهمسة:**\nاكتب يوزر البوت، ثم رسالتك، ثم `@يوزر_الشخص`", parse_mode="Markdown")
            )
        ]
        await update.inline_query.answer(results, cache_time=0)
        return

    whisper_text = match.group(1).strip()
    target_username = match.group(2).strip().lower()
    sender = update.inline_query.from_user

    whisper_id = str(uuid.uuid4())[:8]
    
    # حفظ في الداتا بيز (هنا بنسجل اليوزر عشان معندناش الـ ID بتاعه)
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT INTO whispers (id, sender_id, receiver_id, receiver_username, text) VALUES (?, ?, ?, ?, ?)",
                     (whisper_id, sender.id, None, target_username, whisper_text))
        conn.commit()

    # الهوية الخاصة في الانلاين
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔐 افتح الهمسة", callback_data=f"wsp|{whisper_id}")]])
    msg_text = f"💌 **همسة مغلفة**\nمرسلة خصيصاً إلى: @{target_username}\n\n👇 اضغط على الزر بالأسفل لفتحها"
    
    results = [
        InlineQueryResultArticle(
            id=whisper_id,
            title="🤫 إرسال همسة سرية",
            description=f"إلى: @{target_username} | اضغط لإرسالها للجروب",
            input_message_content=InputTextMessageContent(msg_text, parse_mode="Markdown"),
            reply_markup=kb
        )
    ]
    
    await update.inline_query.answer(results, cache_time=0)

# 4. دالة الاستجابة لزر الهمسة (للنوعين)
async def whisper_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = update.effective_user

    if data.startswith("wsp|"):
        whisper_id = data.split("|")[1]
        
        with sqlite3.connect(DB) as conn:
            cur = conn.execute("SELECT sender_id, receiver_id, receiver_username, text FROM whispers WHERE id=?", (whisper_id,))
            row = cur.fetchone()

        if not row:
            await query.answer("❌ الهمسة دي اتبخرت أو اتمسحت!", show_alert=True)
            return

        sender_id, receiver_id, receiver_username, text = row
        
        # فحص الصلاحية بذكاء
        is_authorized = False
        if user.id == sender_id:
            is_authorized = True
        elif receiver_id and user.id == receiver_id:
            is_authorized = True
        elif receiver_username and user.username and user.username.lower() == receiver_username:
            is_authorized = True

        if is_authorized:
            await query.answer(f"🤫 رسالتك السرية:\n\n{text}", show_alert=True)
        else:
            await query.answer("🚷 دي مش بتاعتك يا بطل، بطل فضول!", show_alert=True)