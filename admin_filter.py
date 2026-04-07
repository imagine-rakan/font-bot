# -*- coding: utf-8 -*-
import logging
import sqlite3
import os
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

# إعدادات قاعدة البيانات والمالك
DB = "bot.db"
OWNER_ID = int(os.getenv("OWNER_ID", 0))

# دالة التحقق الشاملة (تليجرام + قاعدة بيانات البوت)
async def is_admin(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if user_id == OWNER_ID:
        return True
        
    # 1. فحص مشرفين تليجرام الأساسيين
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR):
            return True
    except Exception:
        pass
        
    # 2. فحص مشرفين البوت المرفوعين (من قاعدة البيانات)
    try:
        with sqlite3.connect(DB) as conn:
            cur = conn.execute("SELECT user_id FROM admins WHERE user_id=?", (user_id,))
            if cur.fetchone():
                return True
    except Exception as e:
        logging.error(f"خطأ في فحص قاعدة البيانات: {e}")
        
    return False

# -----------------------------------------
# 1. ميزة حذف الحروف (ء ، ذ) للمشرفين
# -----------------------------------------
async def delete_admin_chars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    text = message.text or message.caption or ""
    if 'ء' in text or 'ذ' in text:
        user_id = message.from_user.id
        chat_id = message.chat.id

        if await is_admin(user_id, chat_id, context):
            try:
                await message.delete()
            except Exception as e:
                logging.warning(f"لم أتمكن من حذف رسالة المشرف: {e}")

# -----------------------------------------
# 2. ميزة رفع مشرف بوت
# -----------------------------------------
async def promote_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    # لازم يرد على رسالة الشخص اللي عايز يرفعه
    if not message or not message.reply_to_message:
        await message.reply_text("⚠️ يرجى الرد على رسالة العضو لرفعه كمشرف بوت.")
        return

    user_id = message.from_user.id
    chat_id = message.chat.id

    # التأكد إن اللي بيستخدم الأمر مشرف أصلاً
    if not await is_admin(user_id, chat_id, context):
        await message.reply_text("🚫 هذا الأمر للمشرفين فقط.")
        return

    target_user = message.reply_to_message.from_user
    
    # إضافة الأيدي لقاعدة البيانات
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT OR IGNORE INTO admins VALUES (?)", (target_user.id,))
        conn.commit()
        
    await message.reply_text(f"✅ تم رفع [{target_user.first_name}](tg://user?id={target_user.id}) كمشرف في البوت بنجاح.", parse_mode="Markdown")

# -----------------------------------------
# 3. ميزة إزالة مشرف بوت
# -----------------------------------------
async def demote_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.reply_to_message:
        await message.reply_text("⚠️ يرجى الرد على رسالة العضو لإزالته.")
        return

    user_id = message.from_user.id
    chat_id = message.chat.id

    if not await is_admin(user_id, chat_id, context):
        await message.reply_text("🚫 هذا الأمر للمشرفين فقط.")
        return

    target_user = message.reply_to_message.from_user
    
    # حذف الأيدي من قاعدة البيانات
    with sqlite3.connect(DB) as conn:
        conn.execute("DELETE FROM admins WHERE user_id=?", (target_user.id,))
        conn.commit()
        
    await message.reply_text(f"🗑 تم إزالة [{target_user.first_name}](tg://user?id={target_user.id}) من إشراف البوت.", parse_mode="Markdown")