# -*- coding: utf-8 -*-
import sqlite3
import json
import logging
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

def init_db(conn):  # بدل db_path
    conn.execute("INSERT OR IGNORE INTO settings VALUES ('anti_designer_enabled', '0')")
    default_data = json.dumps({"html": "", "file_id": None})
    conn.execute("INSERT OR IGNORE INTO settings VALUES ('anti_designer_data', ?)", (default_data,))
    conn.execute("CREATE TABLE IF NOT EXISTS anti_designer_keywords (word TEXT PRIMARY KEY)")
    conn.execute("INSERT OR IGNORE INTO anti_designer_keywords VALUES ('مصمم')")
    conn.execute("INSERT OR IGNORE INTO anti_designer_keywords VALUES ('مصممة')")

def is_enabled(db_path):
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='anti_designer_enabled'").fetchone()
        return row is not None and row[0] == '1'

def toggle(db_path):
    state = is_enabled(db_path)
    new_state = '0' if state else '1'
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE settings SET value=? WHERE key='anti_designer_enabled'", (new_state,))
        conn.commit()
    return new_state == '1'

def get_keywords(db_path):
    with sqlite3.connect(db_path) as conn:
        return [row[0] for row in conn.execute("SELECT word FROM anti_designer_keywords").fetchall()]

def add_keyword(db_path, word):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO anti_designer_keywords VALUES (?)", (word.strip().lower(),))
        conn.commit()

def remove_keyword(db_path, word):
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM anti_designer_keywords WHERE word=?", (word,))
        conn.commit()

def get_sub_menu(db_path):
    enabled = is_enabled(db_path)
    btn_toggle_text = "إيقاف الميزة ❌" if enabled else "تفعيل الميزة ✅"
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_toggle_text, callback_data="anti_designer_toggle")],
        [InlineKeyboardButton("💬 تعيين رسالة الحذف", callback_data="anti_designer_set_msg")],
        [InlineKeyboardButton("➕ إضافة كلمة ممنوعة", callback_data="anti_designer_add_kw")],
        [InlineKeyboardButton("🗑 حذف كلمة ممنوعة", callback_data="anti_designer_show_kw")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="panel_main")]
    ])

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE, is_admin: bool, db_path: str):
    if not is_enabled(db_path) or is_admin:
        return False

    user = update.effective_user
    if not user: return False

    full_name = f"{user.first_name} {user.last_name or ''}".lower()
    
    # جلب الكلمات الممنوعة من القاعدة وفحص الاسم
    keywords = get_keywords(db_path)
    found = any(kw in full_name for kw in keywords)

    if found:
        try:
            await update.message.delete()
        except: pass
        
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='anti_designer_data'").fetchone()
            if not row: return True
            data = json.loads(row[0])

        html_msg = data.get("html", "")
        file_id = data.get("file_id")

        if html_msg or file_id:
            escaped_name = html.escape(user.first_name)
            mention_link = f'<a href="tg://user?id={user.id}">{escaped_name}</a>'
            final_html = html_msg.replace("اشارة", mention_link).replace("إشارة", mention_link)

            try:
                if file_id:
                    await context.bot.send_document(chat_id=update.message.chat_id, document=file_id, caption=final_html, parse_mode=ParseMode.HTML)
                else:
                    await context.bot.send_message(chat_id=update.message.chat_id, text=final_html, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            except: pass
        return True
    return False
