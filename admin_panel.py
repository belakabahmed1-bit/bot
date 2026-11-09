
def get_web_wait_list():
    try:
        from web_server import turso_client
        if turso_client:
            return turso_client.execute("SELECT id, first_name, last_name, phone, school, branch, code, created_at FROM web_users WHERE active = 0 ORDER BY id DESC")
    except Exception as e:
        print("get_web_wait_list error:", e)
    return []
"""لوحة تحكم المشرف (الأستاذ) الكاملة لإدارة البوت والمحتوى والتلاميذ."""
import re
import os
import time
import json
import tempfile
import threading
from datetime import datetime

from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove, InputMediaPhoto
)

import db
from db import conn, cursor, get_setting, set_setting, db_lock
from utils import first_in_album
from admin_builders import (
    esc, _back,
    build_wait_list, build_web_wait_list, build_student_info_card, build_users_list, build_schools_menu, build_school_users_list,
    build_deleted_list, build_ranking_list, build_quick_delete_list, build_quick_restore_list,
    build_units_management, build_unit_content_management, build_homework_management,
    build_unit_homework_list, build_bac_admin_menu, build_files_folders_admin,
    build_notifications_settings, build_chat_inbox_menu, build_student_chat_view, build_user_days_list
)

ADMIN_ID = 5279737033

# حالات البث المباشر والرسائل
is_broadcasting = False
broadcast_message_id = None
target_user_id = None
direct_message_id = None

# حالات الذاكرة المؤقتة لعمليات الإضافة المتعددة
admin_state = {}
admin_upload_cache = {}
admin_video_state = {}


# ===================== دوال الاستعلام والبيانات =====================
def get_users_list():
    """جلب قائمة جميع التلاميذ المفعلين سواء من بوت التلغرام أو من الموقع الإلكتروني."""
    merged = []
    seen_phones = set()

    # 1. التلاميذ المفعلين في الموقع الإلكتروني (Turso web_users active = 1)
    try:
        from web_server import turso_client
        if turso_client:
            web_rows = turso_client.execute("""
                SELECT id, first_name, last_name, school, branch, phone, code, created_at
                FROM web_users
                WHERE active = 1
                ORDER BY id DESC
            """)
            for r in web_rows:
                wid = r[0]
                full_name = f"{r[1]} {r[2]}".strip()
                sch = r[3] or "دراسة عن بعد 🌐"
                br = r[4] or ""
                ph = r[5] or ""
                cd = r[6] or ""
                dt = r[7] or "—"
                school_display = f"{sch} ({br})" if br else sch
                merged.append((f"W-{wid}", full_name, school_display, dt, dt, ph, cd, "web"))
                if ph:
                    seen_phones.add(ph)
    except Exception as e:
        print("get_users_list Turso error:", e)

    # 2. التلاميذ المفعلين في البوت المحلي
    with db_lock:
        try:
            cursor.execute("SELECT user_id, full_name, school, last_active, joined_at, phone, code, source FROM users ORDER BY user_id DESC")
            for r in cursor.fetchall():
                p = r[5] if len(r) > 5 else ""
                if p and p in seen_phones:
                    continue
                merged.append(r)
        except Exception as e:
            print("get_users_list SQLite error:", e)

    return merged


def get_wait_list():
    with db_lock:
        cursor.execute("SELECT user_id, full_name, school, code, created_at, phone, source FROM wait WHERE source != 'web' OR source IS NULL")
        return cursor.fetchall()


def get_deleted_users_list():
    with db_lock:
        cursor.execute("SELECT user_id, full_name, school, joined_at, last_active, deleted_at FROM deleted_users")
        return cursor.fetchall()


def get_user_full_name(user_id):
    with db_lock:
        cursor.execute("SELECT full_name FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        if res:
            return res[0]
        cursor.execute("SELECT full_name FROM deleted_users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        return res[0] if res else "تلميذ"


def get_user_activity_dates(user_id):
    with db_lock:
        cursor.execute("SELECT activity_date FROM user_activity WHERE user_id = ? ORDER BY activity_date DESC", (user_id,))
        return [row[0] for row in cursor.fetchall()]


def get_users_ranking():
    with db_lock:
        cursor.execute("""
            SELECT u.user_id, u.full_name, u.school, COUNT(a.activity_date) as days_count
            FROM users u
            LEFT JOIN user_activity a ON u.user_id = a.user_id
            GROUP BY u.user_id
            ORDER BY days_count DESC
        """)
        return cursor.fetchall()


def delete_user_by_id(user_id):
    str_id = str(user_id).strip()
    # 1. إذا كان تلميذ مسجل من الموقع (W-ID أو معرف رقمي)
    if str_id.startswith("W-") or (str_id.isdigit() and len(str_id) <= 6):
        wid = str_id.replace("W-", "")
        fn = "تلميذ الموقع"
        try:
            from web_server import turso_client
            if turso_client:
                w_info = turso_client.execute("SELECT id, first_name, last_name, phone, school FROM web_users WHERE id = ? OR phone = ?", [wid, str_id])
                if w_info:
                    r = w_info[0]
                    wid = str(r[0])
                    fn = f"{r[1]} {r[2]}".strip()
                    ph = r[3]
                    sch = r[4] or "الموقع الإلكتروني"
                    now_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                    turso_client.execute("UPDATE web_users SET active = -1 WHERE id = ?", [wid])
                    with db_lock:
                        cursor.execute(
                            "INSERT OR REPLACE INTO deleted_users (user_id, full_name, school, joined_at, last_active, deleted_at, phone, source) VALUES (?, ?, ?, ?, ?, ?, ?, 'web')",
                            (int(wid) + 9990000, fn, sch, now_time, now_time, now_time, ph)
                        )
                        conn.commit()
                    return user_id, fn
        except Exception as e:
            print("Delete web user error:", e)
        return user_id, fn

    with db_lock:
        cursor.execute("SELECT user_id, full_name, school, last_active, joined_at FROM users WHERE user_id=?", (user_id,))
        user_data = cursor.fetchone()
        if user_data:
            uid, full_name, school, last_active, joined_at = user_data
            now_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            last_time = last_active if last_active else "غير متوفر"
            join_time = joined_at if joined_at else "غير محدد"
            cursor.execute(
                "INSERT OR REPLACE INTO deleted_users (user_id, full_name, school, joined_at, last_active, deleted_at) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, full_name, school, join_time, last_time, now_time)
            )
            cursor.execute("DELETE FROM user_activity WHERE user_id=?", (uid,))
            cursor.execute("DELETE FROM users WHERE user_id=?", (uid,))
            conn.commit()
            try:
                from web_server import turso_client
                if turso_client and len(user_data) > 5 and user_data[5]:
                    turso_client.execute("UPDATE web_users SET active = -1 WHERE phone = ?", [user_data[5]])
            except Exception:
                pass
            db.user_steps.pop(uid, None)
            return uid, full_name

        cursor.execute("SELECT user_id, full_name, school FROM wait WHERE user_id=?", (user_id,))
        wait_data = cursor.fetchone()
        if wait_data:
            uid, full_name, school = wait_data
            cursor.execute("DELETE FROM wait WHERE user_id=?", (uid,))
            conn.commit()
            db.user_steps.pop(uid, None)
            return uid, full_name

        return None, None


def restore_user_by_id(user_id):
    str_id = str(user_id).strip()
    if str_id.startswith("W-") or (str_id.isdigit() and len(str_id) <= 6):
        wid = str_id.replace("W-", "")
        try:
            from web_server import turso_client
            if turso_client:
                turso_client.execute("UPDATE web_users SET active = 1 WHERE id = ?", [wid])
                return user_id, "تلميذ الموقع"
        except Exception as e:
            print("Restore web user error:", e)
        return user_id, "تلميذ الموقع"

    with db_lock:
        cursor.execute("SELECT user_id, full_name, school, joined_at, last_active FROM deleted_users WHERE user_id=?", (user_id,))
        user_data = cursor.fetchone()
        if user_data:
            uid, full_name, school, joined_at, last_active = user_data
            now_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            cursor.execute(
                "INSERT OR REPLACE INTO users (user_id, full_name, school, last_active, joined_at) VALUES (?, ?, ?, ?, ?)",
                (uid, full_name, school, now_time, joined_at if joined_at else now_time)
            )
            cursor.execute("DELETE FROM deleted_users WHERE user_id = ?", (uid,))
            conn.commit()
            return uid, full_name
        return None, None


# ===================== القوائم الرئيسية للأدمن =====================
def admin_main_menu():
    users_count = len(get_users_list())
    wait_count = len(get_wait_list())
    deleted_count = len(get_deleted_users_list())
    unread_chat = db.get_unread_chat_count(ADMIN_ID)
    chat_badge = f" ({unread_chat} جديد)" if unread_chat > 0 else ""

    markup = InlineKeyboardMarkup(row_width=2)
    web_wait_count = len(get_web_wait_list())
    markup.add(
        InlineKeyboardButton(f"⏳ قائمة انتظار البوت ({wait_count})", callback_data="wait"),
        InlineKeyboardButton(f"🌐 قائمة انتظار الموقع ({web_wait_count})", callback_data="web_wait_menu")
    )
    markup.add(
        InlineKeyboardButton(f"📊 المفعلين ({users_count})", callback_data="users"),
        InlineKeyboardButton("🔑 كشف الرموز السرية والأرقام", callback_data="secret_codes_menu_0")
    )
    markup.add(
        InlineKeyboardButton("⚙️ تسيير الوحدات والدروس", callback_data="manage_units_control"),
        InlineKeyboardButton("📚 إدارة الواجبات المنزلية", callback_data="manage_units_menu")
    )
    markup.add(
        InlineKeyboardButton("📌 الواجبات المفعلة الآن", callback_data="view_active_assignments"),
        InlineKeyboardButton("👁️ رؤية حلول الواجبات", callback_data="view_solutions_units")
    )
    markup.add(
        InlineKeyboardButton("🎥 تسيير الفيديوهات الشارحة", callback_data="manage_videos_menu"),
        InlineKeyboardButton("📂 تسيير مواضيع BAC", callback_data="manage_bac_menu"),
        InlineKeyboardButton("📁 تسيير الملفات المتنوعة", callback_data="admin_open_folder_0")
    )
    markup.add(
        InlineKeyboardButton("🏆 ترتيب أفضل التلاميذ", callback_data="ranking"),
        InlineKeyboardButton("🏫 التلاميذ حسب المدرسة", callback_data="schools_menu")
    )
    markup.add(
        InlineKeyboardButton(f"💬 محادثات التلاميذ{chat_badge}", callback_data="admin_chat_inbox"),
        InlineKeyboardButton(f"🗑️ المحذوفون ({deleted_count})", callback_data="deleted_users")
    )
    markup.add(
        InlineKeyboardButton("⚡ الحذف السريع", callback_data="quick_delete_start"),
        InlineKeyboardButton("⚡ الاسترجاع السريع", callback_data="quick_restore_start")
    )
    markup.add(
        InlineKeyboardButton("❌ حذف تلميذ (ID / اسم)", callback_data="manual_delete_start"),
        InlineKeyboardButton("♻️ استرجاع تلميذ (ID / اسم)", callback_data="restore_start")
    )
    markup.add(
        InlineKeyboardButton("✉️ إرسال رسالة بـ ID", callback_data="send_direct_start"),
        InlineKeyboardButton("📢 إرسال إعلان (Broadcast)", callback_data="broadcast_start")
    )
    markup.add(
        InlineKeyboardButton("👁️ معاينة واجهة التلميذ", callback_data="enter_student_mode"),
        InlineKeyboardButton("💾 نسخة احتياطية للقاعدة", callback_data="backup_db")
    )
    markup.add(
        InlineKeyboardButton("🔔 مركز إعدادات الإشعارات والتواصل", callback_data="notifications_settings")
    )
    return markup


_panel_lock = threading.Lock()
_last_panel = {}


def send_admin_panel(bot, chat_id, text="👑 **لوحة التحكم:**"):
    """يرسل لوحة الأدمن ويحذف النسخة السابقة منها حتى لا تتكدس اللوحات في المحادثة."""
    with _panel_lock:
        old = _last_panel.pop(chat_id, None)
        if old:
            try:
                bot.delete_message(chat_id, old)
            except Exception:
                pass
        msg = bot.send_message(chat_id, text, reply_markup=admin_main_menu(), parse_mode="Markdown")
        _last_panel[chat_id] = msg.message_id
        return msg


def reset_admin_state(chat_id):
    """يلغي أي عملية رفع أو إدخال معلّقة للأدمن (تُستعمل عند /start)."""
    admin_upload_cache.pop(chat_id, None)
    admin_state.pop(chat_id, None)
    try:
        bot_ref = _bot_ref.get("bot")
        if bot_ref:
            bot_ref.clear_step_handler_by_chat_id(chat_id)
    except Exception:
        pass


_bot_ref = {}


def admin_reply_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("القائمة الرئيسية"))
    return markup


def setup_admin_panel_handlers(bot):
    _bot_ref["bot"] = bot

    # استقبال ملفات الرفع (يدعم الألبومات دون ضياع أي ملف)
    @bot.message_handler(
        func=lambda m: m.from_user.id == ADMIN_ID
        and m.chat.id in admin_upload_cache
        and not (m.text or "").startswith("/"),
        content_types=['text', 'photo', 'document', 'video', 'audio', 'voice', 'video_note'])
    def admin_collect_media(message):
        process_collect_media(message, bot)


    # معالجة إدخال فيديو جديد
    @bot.message_handler(
        func=lambda m: m.from_user.id == ADMIN_ID and m.chat.id in admin_video_state and not (m.text or "").startswith("/")
    )
    def admin_video_input_handler(message):
        chat_id = message.chat.id
        st = admin_video_state.get(chat_id, {})
        step = st.get("step")
        text = (message.text or "").strip()

        if step == "title":
            st["title"] = text
            st["step"] = "url"
            bot.send_message(
                chat_id,
                f"✅ **تم تسجيل عنوان الفيديو:**\n`{text}`\n\n"
                "🔗 أرسل الآن **رابط الفيديو على يوتيوب** (أو كود الـ Video ID):\n"
                "✏️ (مثال: `https://youtu.be/9UrdZXS9Cy4`):",
                parse_mode="Markdown"
            )
        elif step == "url":
            st["url"] = text
            st["step"] = "unit"
            bot.send_message(
                chat_id,
                "📚 **اختر الوحدة التابع لها هذا الفيديو:**",
                reply_markup=build_video_units_picker(),
                parse_mode="Markdown"
            )

    @bot.message_handler(func=lambda message: message.text == "🔙 العودة للوحة الأدمن" and message.from_user.id == ADMIN_ID)
    def exit_student_mode(message):
        db.admin_preview.discard(ADMIN_ID)
        bot.send_message(message.chat.id, "✅ عدت إلى وضع الأدمن.", reply_markup=admin_reply_menu())
        send_admin_panel(bot, message.chat.id)

    @bot.message_handler(func=lambda message: message.text == "القائمة الرئيسية" and message.from_user.id == ADMIN_ID)
    def show_admin_panel(message):
        send_admin_panel(bot, message.chat.id)


# ===================== معالجة الأحداث والضغط على الأزرار (Callbacks) =====================
def handle_admin_callback(bot, call):
    global is_broadcasting, broadcast_message_id, target_user_id, direct_message_id

    chat_id = call.message.chat.id
    msg_id = call.message.message_id
    data = call.data

    try:
        # ----------------- العودة للقائمة الرئيسية -----------------
        if data == "admin_home":
            bot.edit_message_text("👑 **لوحة تحكم الأدمين:**", chat_id=chat_id, message_id=msg_id, reply_markup=admin_main_menu(), parse_mode="Markdown")

        # ----------------- معاينة واجهة التلميذ -----------------
        elif data == "enter_student_mode":
            from user_panel import main_menu
            db.admin_preview.add(ADMIN_ID)
            bot.answer_callback_query(call.id, "👁️ وضع معاينة التلميذ")
            bot.send_message(
                chat_id,
                "👁️ **أنت الآن في وضع معاينة التلميذ.**\n"
                "ترى البوت كما يراه التلميذ تماماً. إرسال الحلول والمراسلة معطّلان في هذا الوضع.\n"
                "للرجوع اضغط **🔙 العودة للوحة الأدمن** أسفل الشاشة.",
                reply_markup=main_menu(ADMIN_ID),
                parse_mode="Markdown"
            )

        # ----------------- نسخة احتياطية لقاعدة البيانات -----------------
        elif data == "backup_db":
            bot.answer_callback_query(call.id, "⏳ جاري تجهيز النسخة الاحتياطية...")
            send_db_backup(bot, chat_id)

        # ----------------- 1. قائمة الانتظار -----------------
        
        # ----------------- طلبات تسجيل الموقع والرمز السري -----------------
        elif data == "web_wait_menu":
            rows = get_web_wait_list()
            text, markup = build_web_wait_list(rows)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("ok_web_wait_") or data.startswith("ok_web_phone_"):
            target = data.split("_")[-1]
            try:
                from web_server import turso_client
                if turso_client:
                    if data.startswith("ok_web_phone_"):
                        turso_client.execute("UPDATE web_users SET active = 1 WHERE phone = ?", [target])
                    else:
                        turso_client.execute("UPDATE web_users SET active = 1 WHERE id = ?", [target])
            except Exception as e:
                print("Accept web user error:", e)
            bot.answer_callback_query(call.id, "✅ تم قبول وتفعيل تلميذ الموقع بنجاح!")
            rows = get_web_wait_list()
            text, markup = build_web_wait_list(rows)
            try:
                bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                pass

        elif data.startswith("del_web_wait_") or data.startswith("del_web_phone_"):
            target = data.split("_")[-1]
            try:
                from web_server import turso_client
                if turso_client:
                    if data.startswith("del_web_phone_"):
                        turso_client.execute("UPDATE web_users SET active = -1 WHERE phone = ?", [target])
                    else:
                        turso_client.execute("UPDATE web_users SET active = -1 WHERE id = ?", [target])
            except Exception as e:
                print("Ban web user error:", e)
            bot.answer_callback_query(call.id, "❌ تم حظر / رفض طلب الموقع!")
            rows = get_web_wait_list()
            text, markup = build_web_wait_list(rows)
            try:
                bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                pass

        
        # ----------------- قائمة الرموز السرية والأرقام للتلاميذ -----------------
        elif data.startswith("secret_codes_menu_"):
            page = int(data.split("_")[-1])
            text, markup = build_secret_codes_page(page)
            try:
                bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                pass

        elif data == "find_student_code":
            bot.edit_message_text(
                "🔑 **كشف الرمز السري واستعلام بيانات التلميذ:**\n\n"
                "أرسل الآن **رقم هاتف** التلميذ، أو **اسمه** (أو جزء منه)، أو **الآيدي** الخاص به للبحث فوراً:\n\n"
                "💡 *ملاحظة:* سيبحث النظام في بيانات الموقع والتليجرام معاً ويعرض الرمز السري وحالة التفعيل.",
                chat_id=chat_id,
                message_id=msg_id,
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 إلغاء والعودة", callback_data="admin_home")),
                parse_mode="Markdown"
            )
            admin_state[chat_id] = "find_student_code"
            bot.register_next_step_handler_by_chat_id(chat_id, process_find_student_code, bot)

        elif data == "wait":
            rows = get_wait_list()
            if not rows:
                bot.edit_message_text("🚫 لا توجد طلبات انتظار حالياً.", chat_id=chat_id, message_id=msg_id, reply_markup=admin_main_menu(), parse_mode="Markdown")
                return
            text, markup = build_wait_list(rows, "del_wait")
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("ok_wait_"):
            uid = int(data.split("_")[-1])
            res = db.activate_from_wait(uid)
            if res:
                try:
                    from user_panel import main_menu
                    phone_val = res.get("phone", "") if isinstance(res, dict) else ""
                    code_val = res.get("code", "") if isinstance(res, dict) else ""
                    bot.send_message(
                        uid,
                        f"🎉 **تم قبولك وتفعيل حسابك بنجاح من طرف الأستاذ بلعقاب أسامة!** 🚀\n\n"
                        f"مرحباً بك في عائلة الرياضيات لتحضير البكالوريا 🎓✨\n\n"
                        f"🌐 **بيانات تسجيل دخولك لموقع الأستاذ الإلكتروني:**\n"
                        f"📱 **رقم الهاتف:** `{phone_val}`\n"
                        f"🔑 **الرمز السري:** `{code_val}`\n\n"
                        f"⚠️ **تنبيه:** هذا الرمز السري دائم وخاص بك ولا يمكن تغييره. استعمله دائماً للدخول إلى موقع الأستاذ لمشاهدة الفيديوهات وتحميل التمارين والحلول 📐",
                        reply_markup=main_menu(uid),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    print("Error notifying activated user:", e)
                bot.answer_callback_query(call.id, "تم قبول التلميذ وتفعيل حسابه بنجاح ✅")
            rows = get_wait_list()
            if not rows:
                bot.edit_message_text("🚫 لا توجد طلبات انتظار حالياً.", chat_id=chat_id, message_id=msg_id, reply_markup=admin_main_menu(), parse_mode="Markdown")
            else:
                text, markup = build_wait_list(rows, "del_wait")
                bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("del_wait_"):
            uid = int(data.split("_")[-1])
            delete_user_by_id(uid)
            bot.answer_callback_query(call.id, "تم رفض الطلب ❌")
            try:
                bot.send_message(uid, "❌ **نعتذر منك، تم رفض طلب تسجيلك في البوت من قِبل المشرف.**", parse_mode="Markdown")
            except Exception:
                pass
            rows = get_wait_list()
            if not rows:
                bot.edit_message_text("🚫 لا توجد طلبات انتظار حالياً.", chat_id=chat_id, message_id=msg_id, reply_markup=admin_main_menu(), parse_mode="Markdown")
            else:
                text, markup = build_wait_list(rows, "del_wait")
                bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        # ----------------- 2. قائمة المفعلين -----------------
        elif data == "users":
            rows = get_users_list()
            if not rows:
                bot.edit_message_text("🚫 لا يوجد تلاميذ مفعلين حالياً.", chat_id=chat_id, message_id=msg_id, reply_markup=admin_main_menu(), parse_mode="Markdown")
                return
            text, markup = build_users_list(rows)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("del_user_"):
            raw_target = data.replace("del_user_", "").strip()
            del_id, del_name = delete_user_by_id(raw_target)
            bot.answer_callback_query(call.id, f"تم حذف {del_name or 'التلميذ'} وحظره بنجاح ✅")
            if not str(raw_target).startswith("W-"):
                try:
                    uid = int(raw_target)
                    bot.send_message(uid, "🚫 *تم حذف حسابك من قبل المشرف.*", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
                except Exception:
                    pass
            rows = get_users_list()
            if not rows:
                bot.edit_message_text("🚫 لا يوجد تلاميذ مفعلين.", chat_id=chat_id, message_id=msg_id, reply_markup=admin_main_menu(), parse_mode="Markdown")
            else:
                text, markup = build_users_list(rows)
                bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        # ----------------- 3. الحذف السريع والاسترجاع السريع -----------------
        elif data == "quick_delete_start":
            users = get_users_list()
            if not users:
                bot.answer_callback_query(call.id, "🚫 لا يوجد تلاميذ لحذفهم.")
                return
            text, markup = build_quick_delete_list(users)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("del_quick_") and not data == "del_quick_all":
            raw_target = data.replace("del_quick_", "").strip()
            del_id, del_name = delete_user_by_id(raw_target)
            bot.answer_callback_query(call.id, f"تم حذف {del_name or 'التلميذ'} بنجاح ✅")
            try:
                bot.send_message(uid, "🚫 *تم حذف حسابك.*", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
            except Exception:
                pass
            users = get_users_list()
            if not users:
                bot.edit_message_text("🚫 لا يوجد المزيد من التلاميذ في قائمة المفعلين.", chat_id=chat_id, message_id=msg_id, reply_markup=admin_main_menu())
            else:
                text, markup = build_quick_delete_list(users)
                bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data == "del_quick_all":
            users = get_users_list()
            to_delete = users[:15]
            for u in to_delete:
                delete_user_by_id(u[0])
                try:
                    bot.send_message(u[0], "🚫 *تم حذف حسابك.*", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
                except Exception:
                    pass
            bot.answer_callback_query(call.id, f"✅ تم حذف {len(to_delete)} تلاميذ بنجاح!")
            rem_users = get_users_list()
            if not rem_users:
                bot.edit_message_text("🚫 لا يوجد المزيد من التلاميذ.", chat_id=chat_id, message_id=msg_id, reply_markup=admin_main_menu())
            else:
                text, markup = build_quick_delete_list(rem_users)
                bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data == "quick_restore_start":
            d_users = get_deleted_users_list()
            if not d_users:
                bot.answer_callback_query(call.id, "🚫 لا توجد حسابات محذوفة.")
                return
            text, markup = build_quick_restore_list(d_users)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("res_quick_") and not data == "res_quick_all":
            uid = int(data.split("_")[-1])
            res_id, res_name = restore_user_by_id(uid)
            if res_id:
                bot.answer_callback_query(call.id, f"♻️ تم استرجاع {res_name} بنجاح!")
                try:
                    from user_panel import main_menu
                    bot.send_message(res_id, "✅ *تم استرجاع وتفعيل حسابك بنجاح من قِبل المشرف.*", reply_markup=main_menu(res_id), parse_mode="Markdown")
                except Exception:
                    pass
            d_users = get_deleted_users_list()
            if not d_users:
                bot.edit_message_text("🚫 لا توجد حسابات محذوفة حالياً.", chat_id=chat_id, message_id=msg_id, reply_markup=admin_main_menu())
            else:
                text, markup = build_quick_restore_list(d_users)
                bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data == "res_quick_all":
            d_users = get_deleted_users_list()
            to_restore = d_users[:15]
            for u in to_restore:
                restore_user_by_id(u[0])
                try:
                    from user_panel import main_menu
                    bot.send_message(u[0], "✅ *تم استرجاع وتفعيل حسابك بنجاح.*", reply_markup=main_menu(u[0]), parse_mode="Markdown")
                except Exception:
                    pass
            bot.answer_callback_query(call.id, f"♻️ تم استرجاع {len(to_restore)} حساب بنجاح!")
            rem_d = get_deleted_users_list()
            if not rem_d:
                bot.edit_message_text("🚫 تم استرجاع كافة الحسابات المحذوفة.", chat_id=chat_id, message_id=msg_id, reply_markup=admin_main_menu())
            else:
                text, markup = build_quick_restore_list(rem_d)
                bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        # ----------------- 4. المحذوفون -----------------
        elif data == "deleted_users":
            rows = get_deleted_users_list()
            text, markup = build_deleted_list(rows)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("res_del_"):
            uid = int(data.split("_")[-1])
            res_id, res_name = restore_user_by_id(uid)
            if res_id:
                bot.answer_callback_query(call.id, f"♻️ تم استرجاع {res_name} بنجاح!")
                try:
                    from user_panel import main_menu
                    bot.send_message(res_id, "✅ *تم استرجاع وتفعيل حسابك بنجاح من قِبل المشرف.*", reply_markup=main_menu(res_id), parse_mode="Markdown")
                except Exception:
                    pass
            rows = get_deleted_users_list()
            text, markup = build_deleted_list(rows)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        # ----------------- 5. تسيير الوحدات والدروس والتمارين (بدون إضافة وحدات) -----------------
        elif data == "manage_units_control":
            with db_lock:
                cursor.execute("SELECT id, name, is_active FROM units ORDER BY id ASC")
                units = cursor.fetchall()
            text, markup = build_units_management(units)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("toggle_unit_"):
            u_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT is_active FROM units WHERE id = ?", (u_id,))
                res = cursor.fetchone()
                if res:
                    new_status = 0 if res[0] == 1 else 1
                    cursor.execute("UPDATE units SET is_active = ? WHERE id = ?", (new_status, u_id))
                    conn.commit()
                cursor.execute("SELECT id, name, is_active FROM units ORDER BY id ASC")
                units = cursor.fetchall()
            text, markup = build_units_management(units)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("ctrl_unit_content_"):
            u_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT name FROM units WHERE id = ?", (u_id,))
                u_name = cursor.fetchone()[0]
                cursor.execute("SELECT id, name, is_active, item_type FROM exercises WHERE unit_id = ? AND item_type != 'homework' ORDER BY id ASC", (u_id,))
                items = cursor.fetchall()
            text, markup = build_unit_content_management(u_name, u_id, items)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("toggle_ex_status_"):
            ex_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT is_active, unit_id FROM exercises WHERE id = ?", (ex_id,))
                res = cursor.fetchone()
                if res:
                    new_status = 0 if res[0] == 1 else 1
                    cursor.execute("UPDATE exercises SET is_active = ? WHERE id = ?", (new_status, ex_id))
                    conn.commit()
                    call.data = f"ctrl_unit_content_{res[1]}"
                    handle_admin_callback(bot, call)

        elif data.startswith("del_ex_item_"):
            ex_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT unit_id FROM exercises WHERE id = ?", (ex_id,))
                res = cursor.fetchone()
                if res:
                    u_id = res[0]
                    cursor.execute("DELETE FROM exercises WHERE id = ?", (ex_id,))
                    conn.commit()
                    bot.answer_callback_query(call.id, "✅ تم حذف العنصر بنجاح!")
                    call.data = f"ctrl_unit_content_{u_id}"
                    handle_admin_callback(bot, call)

        # فصل إضافة الدرس عن إضافة التمرين
        elif data.startswith("add_item_kind_"):
            parts = data.split("_")
            kind = parts[3]   # 'lesson' أو 'exercise'
            u_id = int(parts[4])
            kind_label = "الدرس" if kind == "lesson" else "التمرين"
            admin_state[chat_id] = {"item_kind": kind, "unit_id": u_id}
            msg = bot.send_message(chat_id, f"📝 **أدخل عنوان {kind_label} الجديد:**", reply_markup=admin_reply_menu())
            bot.register_next_step_handler(msg, process_start_multi_upload, bot)

        # ----------------- 6. إدارة الواجبات والزر الأصفر الذكي (00/20) -----------------
        elif data == "manage_units_menu":
            with db_lock:
                cursor.execute("""
                    SELECT u.id, u.name, COUNT(CASE WHEN e.is_active = 1 THEN 1 END)
                    FROM units u
                    LEFT JOIN exercises e ON u.id = e.unit_id AND e.item_type = 'homework'
                    GROUP BY u.id
                    ORDER BY u.id ASC
                """)
                units_data = cursor.fetchall()
            text, markup = build_homework_management(units_data)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("admin_hw_unit_"):
            u_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT name FROM units WHERE id = ?", (u_id,))
                u_name = cursor.fetchone()[0]
                cursor.execute("SELECT id, name, is_active FROM exercises WHERE unit_id = ? AND item_type = 'homework' ORDER BY id ASC", (u_id,))
                hws = cursor.fetchall()
            text, markup = build_unit_homework_list(u_name, u_id, hws)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        # تبديل حالة الواجب بنقرة واحدة (مع منطق الـ 00/20 للزر الأصفر)
        elif data.startswith("toggle_hw_status_"):
            ex_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT is_active, unit_id FROM exercises WHERE id = ?", (ex_id,))
                res = cursor.fetchone()
            if res:
                curr = res[0]
                # 🟢 1 ➔ 🟡 2 ➔ 🔴 0 ➔ 🟢 1
                if curr == 1:
                    nxt = 2
                    # وضع علامة 00/20 التلقائية لمن لم يسلم
                    applied = db.apply_auto_zero_to_missing_students(ex_id)
                    bot.answer_callback_query(call.id, f"🟡 تم إنهاء الواجب ومنح 00/20 لـ ({applied}) تلميذ لم يسلموا.")
                elif curr == 2:
                    nxt = 0
                    bot.answer_callback_query(call.id, "🔴 تم إغلاق الواجب وإخفاؤه.")
                else:
                    nxt = 1
                    # إعادة الفتح: حذف علامة 00/20 التلقائية لتمكينهم من الحل
                    removed = db.remove_auto_zero_on_reopen(ex_id)
                    bot.answer_callback_query(call.id, f"🟢 تم فتح الواجب مجدداً وحذف الـ 00/20 لـ ({removed}) تلميذ.")

                with db_lock:
                    cursor.execute("UPDATE exercises SET is_active = ? WHERE id = ?", (nxt, ex_id))
                    conn.commit()
                call.data = f"admin_hw_unit_{res[1]}"
                handle_admin_callback(bot, call)

        elif data.startswith("del_hw_item_"):
            ex_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT unit_id, name FROM exercises WHERE id = ?", (ex_id,))
                res = cursor.fetchone()
                if res:
                    u_id, hw_name = res[0], res[1]
                    cursor.execute("DELETE FROM exercises WHERE id = ?", (ex_id,))
                    # احذف حلول هذا الواجب فقط، وليس حلول واجب آخر يحمل نفس الاسم
                    cursor.execute("SELECT COUNT(*) FROM exercises WHERE name = ?", (hw_name,))
                    if (cursor.fetchone() or [0])[0] == 0:
                        cursor.execute("DELETE FROM submissions WHERE exercise_name = ?", (hw_name,))
                    conn.commit()
                    bot.answer_callback_query(call.id, "✅ تم حذف الواجب بنجاح!")
                    call.data = f"admin_hw_unit_{u_id}"
                    handle_admin_callback(bot, call)

        elif data == "view_active_assignments":
            with db_lock:
                cursor.execute("""
                    SELECT e.id, e.name, u.name
                    FROM exercises e
                    JOIN units u ON e.unit_id = u.id
                    WHERE u.is_active = 1 AND e.is_active = 1 AND e.item_type = 'homework'
                    ORDER BY u.id ASC
                """)
                active_items = cursor.fetchall()
            markup = InlineKeyboardMarkup()
            if not active_items:
                markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="admin_home"))
                bot.edit_message_text("⚠️ **لا توجد أي واجبات مفعلة حالياً.**", chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")
                return

            for ex_id, ex_name, u_name in active_items:
                clean_unit = u_name.replace("📈", "").replace("🔢", "").replace("🎲", "").replace("➗", "").replace("🧮", "").strip()
                markup.add(InlineKeyboardButton(f"📝 {ex_name} ({clean_unit})", callback_data=f"sol_ex_{ex_id}"))

            markup.add(InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_home"))
            bot.edit_message_text("📌 **الواجبات المفعلة حالياً (تستقبل حلول التلاميذ):**\nاضغط على الواجب للاطلاع على حلول الطلاب وتصحيحها:", chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        # ----------------- 7. تصحيح حلول الواجبات -----------------
        elif data == "view_solutions_units":
            with db_lock:
                cursor.execute("SELECT id, name FROM units")
                units = cursor.fetchall()
            markup = InlineKeyboardMarkup()
            for u_id, u_name in units:
                markup.add(InlineKeyboardButton(f"{u_name}", callback_data=f"sol_unit_{u_id}"))
            markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="admin_home"))
            bot.edit_message_text("👁️ **رؤية حلول الواجبات:**\nاختر الوحدة:", chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("sol_unit_"):
            u_id = int(data.replace("sol_unit_", ""))
            with db_lock:
                cursor.execute("SELECT name FROM units WHERE id = ?", (u_id,))
                u_name = cursor.fetchone()[0]
                cursor.execute("SELECT id, name FROM exercises WHERE unit_id = ? AND item_type = 'homework'", (u_id,))
                exs = cursor.fetchall()
            markup = InlineKeyboardMarkup()
            for ex_id, ex_name in exs:
                markup.add(InlineKeyboardButton(f"📝 {ex_name}", callback_data=f"sol_ex_{ex_id}"))
            markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="view_solutions_units"))
            bot.edit_message_text(f"📁 **وحدة {u_name}:**\nاختر الواجب لاستعراض حلول التلاميذ:", chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("sol_ex_"):
            ex_id = int(data.replace("sol_ex_", ""))
            with db_lock:
                cursor.execute("SELECT name, unit_id FROM exercises WHERE id = ?", (ex_id,))
                ex_info = cursor.fetchone()
            if not ex_info:
                return
            ex_name, u_id = ex_info
            clean_ex_name = ex_name.replace("حل", "").strip()

            with db_lock:
                cursor.execute("""
                    SELECT DISTINCT s.student_id, s.student_name, s.is_read, s.has_grade, s.grade_text, COALESCE(u.school, 'غير مسجل')
                    FROM submissions s
                    LEFT JOIN users u ON s.student_id = u.user_id
                    WHERE s.exercise_name = ?
                """, (ex_name,))
                students = cursor.fetchall()

            markup = InlineKeyboardMarkup()
            if not students:
                markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="view_active_assignments"))
                bot.edit_message_text(f"🚫 لا توجد حلول مرسلة لهذا الواجب ({ex_name}).", chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")
                return

            for s_id, s_name, is_read, has_grade, grade_text, school_name in students:
                if has_grade == 1:
                    icon = "💯"
                    extra = f" (العلامة: {grade_text})" if grade_text else " (تم التصحيح)"
                elif is_read == 1:
                    icon = "👁️"
                    extra = " (قيد المراجعة)"
                else:
                    icon = "📥"
                    extra = " (جديد)"
                markup.add(InlineKeyboardButton(f"{icon} {s_name} ({school_name}){extra}", callback_data=f"show_sub_{s_id}_{ex_id}"))

            markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="view_active_assignments"))
            bot.edit_message_text(f"📝 **حلول التلاميذ لواجب ({ex_name}):**\nدلالات: 📥 جديد | 👁️ قيد الاطلاع | 💯 تم التقييم والعلامة", chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("show_sub_"):
            parts = data.split("_")
            s_id, ex_id = int(parts[2]), int(parts[3])
            with db_lock:
                cursor.execute("SELECT name FROM exercises WHERE id = ?", (ex_id,))
                ex_info = cursor.fetchone()
            ex_name = ex_info[0] if ex_info else "الواجب"
            clean_ex_name = ex_name.replace("حل", "").strip()

            with db_lock:
                cursor.execute("UPDATE submissions SET is_read = 1 WHERE student_id = ? AND exercise_name = ?",
                               (s_id, ex_name))
                conn.commit()
                cursor.execute("SELECT photo_id, student_name, grade_text, notes_history FROM submissions WHERE student_id = ? AND exercise_name = ?",
                               (s_id, ex_name))
                subs = cursor.fetchall()

            if subs:
                s_name = subs[0][1]
                grade_txt = subs[0][2]
                notes = subs[0][3] or "لا توجد ملاحظات مسجلة بعد."
                photo_ids = [r[0] for r in subs if r[0] != "NO_SUBMISSION"]

                if photo_ids:
                    media_group = []
                    for idx, p_id in enumerate(photo_ids[:10]):
                        cap = f"👤 **التلميذ:** {s_name}\n📝 **الواجب:** {ex_name}\n🖼️ صفحة ({idx+1}/{len(photo_ids)})\n⭐ **العلامة:** {grade_txt or 'لم تقيم بعد'}" if idx == 0 else ""
                        media_group.append(InputMediaPhoto(media=p_id, caption=cap, parse_mode="Markdown"))
                    try:
                        bot.send_media_group(chat_id, media_group)
                    except Exception as e:
                        bot.send_message(chat_id, f"❌ خطأ في عرض الصور: {e}")
                else:
                    bot.send_message(chat_id, f"⚠️ لم يتم إرسال أي صور لهذا الواجب (تخلف عن التسليم).")

                ctrl_markup = InlineKeyboardMarkup()
                ctrl_markup.add(
                    InlineKeyboardButton("⭐ وضع / تعديل العلامة", callback_data=f"grade_prompt_{s_id}_{ex_id}"),
                    InlineKeyboardButton("💬 إضافة ملاحظة / توجيه", callback_data=f"note_prompt_{s_id}_{ex_id}")
                )
                bot.send_message(
                    chat_id,
                    f"⚙️ **لوحة تقييم حل ({s_name}):**\n\n"
                    f"⭐ **العلامة الحالية:** `{grade_txt or 'غير محددة'}`\n"
                    f"📝 **سجل الملاحظات والتوجيهات:**\n{notes}",
                    reply_markup=ctrl_markup,
                    parse_mode="Markdown"
                )
            bot.answer_callback_query(call.id)

        elif data.startswith("grade_prompt_"):
            parts = data.split("_")
            s_id, ex_id = int(parts[2]), int(parts[3])
            msg = bot.send_message(chat_id, f"⭐ **أرسل العلامة للتلميذ (مثال: 18.5/20):**", reply_markup=admin_reply_menu())
            bot.register_next_step_handler(msg, process_save_grade, bot, s_id, ex_id)

        elif data.startswith("note_prompt_"):
            parts = data.split("_")
            s_id, ex_id = int(parts[2]), int(parts[3])
            msg = bot.send_message(chat_id, f"💬 **أرسل ملاحظتك أو توجيهك للتلميذ حول الواجب:**", reply_markup=admin_reply_menu())
            bot.register_next_step_handler(msg, process_save_note, bot, s_id, ex_id)

        # ----------------- 8. تسيير مواضيع البكالوريا -----------------
        elif data == "manage_videos_menu":
            text, markup = build_admin_videos_menu()
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("toggle_vid_"):
            vid_id = int(data.split("_")[-1])
            new_val = db.toggle_video_active(vid_id)
            ans = "🟢 تم تفعيل الفيديو للجميع في الموقع والبوت" if new_val == 1 else "🔴 تم إغلاق الفيديو عن الجميع"
            bot.answer_callback_query(call.id, ans)
            text, markup = build_admin_videos_menu()
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("del_vid_"):
            vid_id = int(data.split("_")[-1])
            db.delete_video(vid_id)
            bot.answer_callback_query(call.id, "تم حذف الفيديو بنجاح 🗑️")
            text, markup = build_admin_videos_menu()
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data == "add_new_video_start":
            admin_video_state[chat_id] = {"step": "title"}
            bot.send_message(
                chat_id,
                "🎥 **إضافة فيديو تعليمي جديد:**\n\n"
                "✏️ أرسل **عنوان الفيديو** (مثال: ملخص شامل لجميع حالات إشارة المشتقة):",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )

        elif data.startswith("vid_u_"):
            u_id = int(data.split("_")[-1])
            st = admin_video_state.pop(chat_id, None)
            if not st or "title" not in st:
                bot.answer_callback_query(call.id, "انتهت الجلسة، ابدأ من جديد.")
            else:
                unit_names = {
                    1: "الدوال العددية 📉", 2: "الدلة الأسية 📈", 3: "الدلة اللوغارتمية 📊",
                    4: "المتتاليات 🔢", 5: "الاحتمالات 🎲", 6: "القسمة في Z ➗",
                    7: "الأعداد المركبة 🧮", 0: "شرح عام 🎬"
                }
                u_name = unit_names.get(u_id, "شرح عام 🎬")
                db.add_video(st["title"], st.get("url", ""), u_id, u_name)
                bot.send_message(
                    chat_id,
                    f"🎉 **تمت إضافة الفيديو بنجاح!**\n\n"
                    f"🎬 **العنوان:** {st['title']}\n"
                    f"📚 **الوحدة:** {u_name}\n"
                    f"🟢 **الحالة:** مفعّل ومتاح للجميع في الموقع وبوت التليجرام 🚀",
                    reply_markup=admin_reply_menu(),
                    parse_mode="Markdown"
                )
                text, markup = build_admin_videos_menu()
                bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

        elif data == "manage_bac_menu":
            with db_lock:
                cursor.execute("SELECT id, year, stream, title FROM bac_topics ORDER BY year DESC, id DESC")
                topics = cursor.fetchall()
            text, markup = build_bac_admin_menu(topics)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("del_bac_"):
            t_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("DELETE FROM bac_topics WHERE id = ?", (t_id,))
                conn.commit()
            bot.answer_callback_query(call.id, "✅ تم حذف موضوع البكالوريا بنجاح!")
            call.data = "manage_bac_menu"
            handle_admin_callback(bot, call)

        elif data.startswith("view_bac_"):
            t_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT title, pdf_file_id FROM bac_topics WHERE id = ?", (t_id,))
                row = cursor.fetchone()
            if row and row[1]:
                bot.send_document(chat_id, row[1], caption=f"📄 **{row[0]}**", parse_mode="Markdown")
            bot.answer_callback_query(call.id)

        elif data == "add_bac_prompt":
            streams_markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            streams_markup.add("علوم تجريبية", "تقني رياضي", "رياضيات", "القائمة الرئيسية")
            msg = bot.send_message(chat_id, "🎓 **اختر الشعبة أو اكتبها:**", reply_markup=streams_markup)
            bot.register_next_step_handler(msg, process_add_bac_stream, bot)

        # ----------------- 9. تسيير الملفات المتنوعة -----------------
        elif data.startswith("admin_open_folder_"):
            f_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT id, name FROM file_folders WHERE parent_id = ?", (f_id,))
                folders = cursor.fetchall()
                cursor.execute("SELECT id, title, file_type FROM custom_files WHERE folder_id = ?", (f_id,))
                files = cursor.fetchall()
            text, markup = build_files_folders_admin(folders, files, f_id)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("admin_create_folder_"):
            parent_id = int(data.split("_")[-1])
            msg = bot.send_message(chat_id, "📁 **أرسل اسم المجلد الجديد:**", reply_markup=admin_reply_menu())
            bot.register_next_step_handler(msg, process_create_folder, bot, parent_id)

        elif data.startswith("admin_upload_file_"):
            f_id = int(data.split("_")[-1])
            msg = bot.send_message(chat_id, "📄 **أرسل عنوان أو اسم الملف أولاً:**", reply_markup=admin_reply_menu())
            bot.register_next_step_handler(msg, process_upload_file_title, bot, f_id)

        elif data.startswith("admin_del_folder_"):
            f_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("DELETE FROM file_folders WHERE id = ?", (f_id,))
                cursor.execute("DELETE FROM custom_files WHERE folder_id = ?", (f_id,))
                conn.commit()
            bot.answer_callback_query(call.id, "✅ تم حذف المجلد ومحتوياته!")
            call.data = "admin_open_folder_0"
            handle_admin_callback(bot, call)

        elif data.startswith("admin_del_file_"):
            fid = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("DELETE FROM custom_files WHERE id = ?", (fid,))
                conn.commit()
            bot.answer_callback_query(call.id, "✅ تم حذف الملف بنجاح!")
            call.data = "admin_open_folder_0"
            handle_admin_callback(bot, call)

        elif data.startswith("admin_view_file_"):
            fid = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT title, file_id, file_type FROM custom_files WHERE id = ?", (fid,))
                row = cursor.fetchone()
            if row:
                t, f_id, f_type = row
                if f_type == "photo":
                    bot.send_photo(chat_id, f_id, caption=f"🖼️ **{t}**", parse_mode="Markdown")
                else:
                    bot.send_document(chat_id, f_id, caption=f"📄 **{t}**", parse_mode="Markdown")
            bot.answer_callback_query(call.id)

        # ----------------- 10. إعدادات الإشعارات والتواصل -----------------
        elif data == "notifications_settings":
            text, markup = build_notifications_settings()
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("toggle_setting_"):
            setting_key = data.replace("toggle_setting_", "")
            curr = get_setting(setting_key, "1")
            new_val = "0" if curr == "1" else "1"
            set_setting(setting_key, new_val)
            bot.answer_callback_query(call.id, "تم تحديث الإعداد بنجاح ✅")
            text, markup = build_notifications_settings()
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        # ----------------- 11. صندوق محادثات واستفسارات التلاميذ (Instagram Style) -----------------
        elif data == "admin_chat_inbox":
            db.cleanup_old_chat_messages()
            with db_lock:
                # تجميع المحادثات حسب التلميذ خلال آخر 24 ساعة
                cursor.execute("""
                    SELECT
                        CASE WHEN m.sender_id = ? THEN m.receiver_id ELSE m.sender_id END as stu_id,
                        CASE WHEN m.sender_id = ? THEN (SELECT full_name FROM users WHERE user_id = m.receiver_id) ELSE m.sender_name END as stu_name,
                        COALESCE(u.school, 'غير مسجل'),
                        SUM(CASE WHEN m.is_read = 0 AND m.receiver_id = ? THEN 1 ELSE 0 END),
                        MAX(m.message_text),
                        MAX(m.created_at)
                    FROM chat_messages m
                    LEFT JOIN users u ON u.user_id = (CASE WHEN m.sender_id = ? THEN m.receiver_id ELSE m.sender_id END)
                    WHERE m.sender_id = ? OR m.receiver_id = ?
                    GROUP BY stu_id
                    ORDER BY MAX(m.id) DESC
                """, (ADMIN_ID, ADMIN_ID, ADMIN_ID, ADMIN_ID, ADMIN_ID, ADMIN_ID))
                convs = cursor.fetchall()

            text, markup = build_chat_inbox_menu(convs)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("open_chat_"):
            s_id = int(data.replace("open_chat_", ""))
            s_name = get_user_full_name(s_id)

            db.cleanup_old_chat_messages()
            with db_lock:
                cursor.execute("UPDATE chat_messages SET is_read = 1 WHERE sender_id = ? AND receiver_id = ?", (s_id, ADMIN_ID))
                conn.commit()
                cursor.execute("""
                    SELECT sender_id, sender_name, message_text, created_at, file_id, file_type
                    FROM chat_messages
                    WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)
                    ORDER BY id ASC
                """, (s_id, ADMIN_ID, ADMIN_ID, s_id))
                messages = cursor.fetchall()

            text, markup = build_student_chat_view(s_id, s_name, messages)
            try:
                bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

            # إرسال الصور والملفات المحفوظة من التلميذ
            for row in messages:
                f_id = row[4] if len(row) > 4 else None
                f_type = row[5] if len(row) > 5 else None
                s_n = row[1]
                if f_id and f_type == "photo":
                    try:
                        bot.send_photo(chat_id, f_id, caption=f"📷 صورة من {s_n}", protect_content=True)
                    except Exception:
                        pass
                elif f_id and f_type == "document":
                    try:
                        bot.send_document(chat_id, f_id, caption=f"📄 ملف من {s_n}", protect_content=True)
                    except Exception:
                        pass

        elif data.startswith("reply_chat_"):
            s_id = int(data.replace("reply_chat_", ""))
            s_name = get_user_full_name(s_id)
            msg = bot.send_message(chat_id, f"✍️ **اكتب ردك الموجه للتلميذ ({s_name}):**", reply_markup=admin_reply_menu(), parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_send_chat_reply, bot, s_id, s_name)

        elif data.startswith("admin_clear_chat_"):
            s_id = int(data.replace("admin_clear_chat_", ""))
            db.delete_chat_history(s_id)
            bot.answer_callback_query(call.id, "🗑️ تم مسح محادثة التلميذ بنجاح!")
            call.data = f"open_chat_{s_id}"
            handle_admin_callback(bot, call)

        # ----------------- 12. ترتيب وإحصائيات -----------------
        elif data == "ranking":
            rows = get_users_ranking()
            if not rows:
                bot.edit_message_text("🚫 لا توجد بيانات نشاط مسجلة بعد.", chat_id=chat_id, message_id=msg_id, reply_markup=admin_main_menu())
                return
            text, markup = build_ranking_list(rows)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("UD_"):
            raw_uid = data.split("UD_")[-1]
            if raw_uid.startswith("W-"):
                bot.answer_callback_query(call.id, "📊 تلميذ مسجل من الموقع: نشاطه مسجل في المنصة الإلكترونية.", show_alert=True)
                return
            uid = int(raw_uid)
            name = get_user_full_name(uid)
            dates = get_user_activity_dates(uid)
            text, markup = build_user_days_list(uid, name, dates)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")
            bot.answer_callback_query(call.id)

        elif data == "schools_menu":
            markup = build_schools_menu()
            bot.edit_message_text("🏫 **اختر المدرسة لعرض تلاميذها:**", chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("school_") and not data.startswith("school_msg"):
            school_name = data.replace("school_", "")
            with db_lock:
                cursor.execute("SELECT user_id, full_name, school, last_active, joined_at FROM users WHERE school LIKE ?", (f"{school_name}%",))
                rows = cursor.fetchall()
            text, markup = build_school_users_list(school_name, rows)
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("msg_school_"):
            school_name = data.replace("msg_school_", "")
            with db_lock:
                cursor.execute("SELECT COUNT(*) FROM users WHERE school LIKE ?", (f"{school_name}%",))
                cnt = cursor.fetchone()[0]
            msg = bot.send_message(
                chat_id,
                f"📢 **إرسال رسالة جماعية لتلاميذ مدرسة:**\n*{esc(school_name)}*\n"
                f"({cnt} تلميذ)\n\n"
                "أرسل نص أو صورة أو ملف الرسالة:",
                reply_markup=admin_reply_menu(),
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(msg, process_school_broadcast, bot, school_name)

        # ----------------- 13. الحذف والاسترجاع بالـ ID -----------------
        elif data == "manual_delete_start":
            msg = bot.send_message(
                chat_id,
                "👤 **أرسل الـ ID (أو عدة IDs مفصولة بمسافات)، أو اسم التلميذ للحذف:**",
                reply_markup=admin_reply_menu(),
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(msg, process_manual_delete, bot)

        elif data == "restore_start":
            msg = bot.send_message(
                chat_id,
                "♻️ **أرسل الـ ID (أو عدة IDs مفصولة بمسافات)، أو اسم التلميذ للاسترجاع:**",
                reply_markup=admin_reply_menu(),
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(msg, process_manual_restore, bot)

        # ----------------- 14. مراسلة بـ ID وبث عام مع رسائل تأكيد -----------------
        elif data.startswith("msg_user_"):
            raw_uid = data.split("msg_user_")[-1]
            if raw_uid.startswith("W-"):
                wid = raw_uid.replace("W-", "")
                st_info = ""
                try:
                    from web_server import turso_client
                    if turso_client:
                        w_r = turso_client.execute("SELECT first_name, last_name, phone FROM web_users WHERE id = ?", [wid])
                        if w_r:
                            st_info = f"👤 {w_r[0][0]} {w_r[0][1]}\n📱 الهاتف: {w_r[0][2]}"
                except Exception:
                    pass
                alert_text = f"🌐 تلميذ مسجل عبر الموقع الإلكتروني:\n{st_info}\n\nتواصل معه عبر هاتفه أو إنستغرام." if st_info else "🌐 هذا التلميذ مسجل عبر الموقع الإلكتروني."
                bot.answer_callback_query(call.id, alert_text, show_alert=True)
                return
            uid = int(raw_uid)
            target_user_id = uid
            name = get_user_full_name(uid)
            bot.answer_callback_query(call.id)
            msg = bot.send_message(
                chat_id,
                f"📝 **أرسل الآن الرسالة (نص، صورة، أو ملف) الموجهة لـ ({esc(name)}):**",
                reply_markup=admin_reply_menu(),
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(msg, prepare_direct_message, bot)

        elif data == "send_direct_start":
            msg = bot.send_message(chat_id, "👤 **أرسل رقم الـ ID الخاص بالتلميذ:**", reply_markup=admin_reply_menu(), parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_get_user_id_for_msg, bot)

        elif data == "confirm_direct":
            bot.answer_callback_query(call.id, "🚀 جاري الإرسال...")
            try:
                bot.copy_message(chat_id=target_user_id, from_chat_id=chat_id, message_id=direct_message_id)
                bot.send_message(chat_id, f"✅ **تم إرسال الرسالة بنجاح إلى التلميذ (ID: `{target_user_id}`):**", reply_markup=admin_main_menu(), parse_mode="Markdown")
            except Exception as e:
                bot.send_message(chat_id, f"❌ **فشل الإرسال:**\n`{e}`", reply_markup=admin_main_menu(), parse_mode="Markdown")

        elif data == "broadcast_start":
            msg = bot.send_message(chat_id, "📢 **أرسل نص أو ملف الإعلان الذي ترغب في بثه للجميع:**", reply_markup=admin_reply_menu(), parse_mode="Markdown")
            bot.register_next_step_handler(msg, prepare_broadcast, bot)

        elif data == "confirm_broadcast":
            bot.answer_callback_query(call.id, "🚀 جاري البث لجميع التلاميذ...")
            start_broadcasting(bot, chat_id, broadcast_message_id)

        elif data == "stop_broadcast":
            is_broadcasting = False
            bot.answer_callback_query(call.id, "🛑 تم إيقاف البث.")

        elif data.startswith("add_hw_prompt_"):
            u_id = int(data.split("_")[-1])
            admin_state[chat_id] = {"item_kind": "homework", "unit_id": u_id}
            msg = bot.send_message(chat_id, "📝 **أدخل عنوان الواجب المنزلي الجديد:**", reply_markup=admin_reply_menu(), parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_start_multi_upload, bot)

        elif data == "add_hw_unit_prompt":
            msg = bot.send_message(
                chat_id,
                "➕ **أدخل اسم الوحدة الجديدة:**\n(مثال: الدوال المثلثية 📐)",
                reply_markup=admin_reply_menu(),
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(msg, process_add_hw_unit, bot)

        elif data.startswith("del_hw_unit_"):
            u_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT name FROM units WHERE id = ?", (u_id,))
                row = cursor.fetchone()
            if row:
                u_name = row[0]
                markup_confirm = InlineKeyboardMarkup()
                markup_confirm.row(
                    InlineKeyboardButton("✅ نعم، احذف الوحدة", callback_data=f"confirm_del_hw_unit_{u_id}"),
                    InlineKeyboardButton("❌ إلغاء", callback_data="manage_units_menu")
                )
                bot.edit_message_text(
                    f"⚠️ **تأكيد حذف الوحدة:**\n*{esc(u_name)}*\n\n"
                    "سيتم حذف الوحدة وجميع واجباتها نهائياً. هل أنت متأكد؟",
                    chat_id=chat_id, message_id=msg_id,
                    reply_markup=markup_confirm,
                    parse_mode="Markdown"
                )

        elif data.startswith("confirm_del_hw_unit_"):
            u_id = int(data.split("_")[-1])
            with db_lock:
                cursor.execute("SELECT name FROM units WHERE id = ?", (u_id,))
                row = cursor.fetchone()
                if row:
                    cursor.execute("DELETE FROM exercises WHERE unit_id = ? AND item_type = 'homework'", (u_id,))
                    cursor.execute("DELETE FROM units WHERE id = ?", (u_id,))
                    conn.commit()
                    bot.answer_callback_query(call.id, f"✅ تم حذف الوحدة ({row[0]}) وواجباتها.")
            call.data = "manage_units_menu"
            handle_admin_callback(bot, call)

    except Exception as e:
        print(f"Error in handle_admin_callback: {e}")
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass


def send_db_backup(bot, chat_id):
    """ينشئ نسخة آمنة من قاعدة البيانات ويرسلها للأدمن."""
    tmp_path = None
    rows_count = 0
    try:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, f"users_backup_{stamp}.db")

        rows_count = db.export_to_sqlite(tmp_path)

        size_kb = os.path.getsize(tmp_path) / 1024
        with open(tmp_path, "rb") as f:
            bot.send_document(
                chat_id,
                f,
                visible_file_name=f"users_backup_{stamp}.db",
                caption=(
                    f"💾 نسخة احتياطية من قاعدة البيانات\n"
                    f"🗓️ {stamp}\n"
                    f"📦 الحجم: {size_kb:.1f} KB | الصفوف: {rows_count}"
                )
            )
    except Exception as e:
        bot.send_message(chat_id, f"❌ فشل إنشاء النسخة الاحتياطية:\n{e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
                os.rmdir(os.path.dirname(tmp_path))
            except Exception:
                pass


# ===================== الخطوات التفاعلية (Next Step Handlers) =====================

# 0. إضافة وحدة جديدة في قسم الواجبات فقط
def process_add_hw_unit(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    unit_name = (message.text or "").strip()
    if not unit_name:
        bot.send_message(chat_id, "❌ اسم الوحدة لا يمكن أن يكون فارغاً.", reply_markup=admin_reply_menu())
        return
    with db_lock:
        try:
            cursor.execute("INSERT INTO units (name, is_active) VALUES (?, 1)", (unit_name,))
            conn.commit()
            bot.send_message(chat_id, f"✅ **تم إضافة الوحدة:** *{esc(unit_name)}* بنجاح!", reply_markup=admin_reply_menu(), parse_mode="Markdown")
        except Exception:
            bot.send_message(chat_id, "⚠️ هذه الوحدة موجودة مسبقاً أو حدث خطأ.", reply_markup=admin_reply_menu())
    send_admin_panel(bot, chat_id)


# 1. إضافة المحتوى (درس أو تمرين أو واجب) مع دعم إرسال عدة صور أو عدة ملفات
def process_start_multi_upload(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return

    title = (message.text or "").strip()
    if not title:
        retry = bot.send_message(chat_id, "❌ أرسل العنوان كنص من فضلك:", reply_markup=admin_reply_menu())
        bot.register_next_step_handler(retry, process_start_multi_upload, bot)
        return
    st = admin_state.get(chat_id, {})
    kind = st.get("item_kind", "lesson")
    u_id = st.get("unit_id", 1)

    admin_upload_cache[chat_id] = {
        "title": title,
        "kind": kind,
        "unit_id": u_id,
        "media": []
    }

    kind_label = "الدرس" if kind == "lesson" else ("الواجب" if kind == "homework" else "التمرين")
    markup = ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("✅ تأكيد الإرسال والانتهاء"), KeyboardButton("القائمة الرئيسية"))
    msg = bot.send_message(
        chat_id,
        f"📂 لقد اخترت عنوان {kind_label}: (*{esc(title)}*)\n\n"
        "الآن أرسل **ملفات PDF أو الصور** الخاصة به (يمكنك إرسال عدة ملفات أو صور متتابعة).\n"
        "وعند الانتهاء اضغط على **(✅ تأكيد الإرسال والانتهاء)** أدناه لحفظ ونشر المحتوى:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


def process_collect_media(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        admin_upload_cache.pop(chat_id, None)
        send_admin_panel(bot, chat_id)
        return

    if message.text == "✅ تأكيد الإرسال والانتهاء":
        cache = admin_upload_cache.get(chat_id)
        if not cache or not cache["media"]:
            bot.send_message(chat_id, "⚠️ لم تقم برفع أي ملفات أو صور بعد! يرجى إرسال ملف أولاً.", reply_markup=admin_reply_menu())
            return
        admin_upload_cache.pop(chat_id, None)

        title = cache["title"]
        kind = cache["kind"]
        u_id = cache["unit_id"]
        media_list = cache["media"]

        primary_file = media_list[0]["file_id"]
        extra_json = json.dumps(media_list)

        with db_lock:
            cursor.execute(
                "INSERT INTO exercises (unit_id, name, is_active, pdf_file_id, item_type, extra_media) VALUES (?, ?, 1, ?, ?, ?)",
                (u_id, title, primary_file, kind, extra_json)
            )
            conn.commit()
            cursor.execute("SELECT name FROM units WHERE id = ?", (u_id,))
            u_name = cursor.fetchone()[0]

        kind_label = "الدرس" if kind == "lesson" else ("الواجب" if kind == "homework" else "التمرين")
        bot.send_message(chat_id, f"✅ **تم حفظ ونشر {kind_label} ({title}) بنجاح!** (عدد المرفقات: {len(media_list)})", reply_markup=admin_reply_menu(), parse_mode="Markdown")
        send_admin_panel(bot, chat_id)

        # إشعار للطلاب
        if kind == "homework" and get_setting("notify_homework", "1") == "1":
            notify_all_students(bot, f"📝 **تنبيه واجب منزلي جديد!**\n\n• الوحدة: *{esc(u_name)}*\n• الواجب: *{esc(title)}*\n\n📤 يمكنك الاطلاع عليه وإرسال حلك عبر البوت.")
        elif kind != "homework" and get_setting("notify_lessons", "1") == "1":
            prefix = "📘 درس جديد" if kind == "lesson" else "📝 تمرين جديد"
            notify_all_students(bot, f"{prefix}!\n\n• الوحدة: *{esc(u_name)}*\n• العنوان: *{esc(title)}*\n\n📚 متاح الآن للتحميل والمراجعة.")
        return

    cache = admin_upload_cache.get(chat_id)
    if cache is None:
        bot.send_message(chat_id, "انتهت العملية، ابدأ من جديد.", reply_markup=admin_reply_menu())
        return

    new_item = None
    if message.document:
        new_item = {"type": "document", "file_id": message.document.file_id}
    elif message.photo:
        new_item = {"type": "photo", "file_id": message.photo[-1].file_id}

    if new_item is None:
        bot.send_message(chat_id, "❌ يرجى إرسال ملف PDF أو صورة صالحة.")
        return

    if all(m["file_id"] != new_item["file_id"] for m in cache["media"]):
        cache["media"].append(new_item)

    # الألبوم يصل كرسائل منفصلة: نرد مرة واحدة فقط
    if first_in_album(message):
        bot.send_message(
            chat_id,
            f"📥 تم استلام المرفقات (العدد الحالي: {len(cache['media'])}).\n"
            "أرسل المزيد أو اضغط **(✅ تأكيد الإرسال والانتهاء)**:",
            parse_mode="Markdown"
        )


# 2. الحذف والاسترجاع بالـ ID
def process_manual_delete(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return

    query = (message.text or "").strip()
    id_matches = re.findall(r"\b\d{4,15}\b", query)
    deleted_list = []

    if id_matches:
        for uid_str in id_matches:
            uid = int(uid_str)
            del_id, del_name = delete_user_by_id(uid)
            if del_id:
                deleted_list.append((del_id, del_name))
                try:
                    bot.send_message(del_id, "🚫 *تم حذف حسابك من قبل المشرف.*", parse_mode="Markdown")
                except Exception:
                    pass

        if deleted_list:
            resp = f"✅ **تم حذف {len(deleted_list)} حساب بنجاح عبر ID:**\n\n"
            for u_id, u_name in deleted_list:
                resp += f"• 👤 {esc(u_name)} (ID: `{u_id}`)\n"
            bot.send_message(chat_id, resp, parse_mode="Markdown", reply_markup=admin_main_menu())
        else:
            bot.send_message(chat_id, "❌ لم يتم العثور على أي تلميذ بهذه الـ IDs.", reply_markup=admin_main_menu())
        return

    # فحص وحذف تلاميذ الموقع أيضاً
    try:
        from web_server import turso_client
        if turso_client:
            web_matches = turso_client.execute("""
                SELECT id, first_name, last_name, phone FROM web_users
                WHERE (first_name || ' ' || last_name) LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR phone = ? OR id = ?
            """, [f"%{query}%", f"%{query}%", f"%{query}%", query, query.replace("W-", "")])
            for wr in web_matches:
                wid = wr[0]
                wname = f"{wr[1]} {wr[2]}".strip()
                delete_user_by_id(f"W-{wid}")
                deleted_list.append((f"W-{wid}", f"{wname} (موقع)"))
    except Exception as e_wm:
        print("Manual delete web error:", e_wm)

    with db_lock:
        cursor.execute("SELECT user_id, full_name FROM users WHERE full_name LIKE ?", (f"%{query}%",))
        matched = cursor.fetchall()
    if matched:
        for uid, name in matched:
            delete_user_by_id(uid)
            deleted_list.append((uid, name))
            try:
                bot.send_message(uid, "🚫 *تم حذف حسابك من قبل المشرف.*", parse_mode="Markdown")
            except Exception:
                pass
        resp = f"✅ **تم العثور على وحذف {len(deleted_list)} حساب بالاسم ({query}):**\n\n"
        for u_id, u_name in deleted_list:
            resp += f"• 👤 {esc(u_name)} (ID: `{u_id}`)\n"
        bot.send_message(chat_id, resp, parse_mode="Markdown", reply_markup=admin_main_menu())
    else:
        bot.send_message(chat_id, f"❌ لم يتم العثور على أي تلميذ بالاسم ({query}).", reply_markup=admin_main_menu())


def process_manual_restore(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return

    query = (message.text or "").strip()
    id_matches = re.findall(r"\b\d{4,15}\b", query)
    restored_list = []

    if id_matches:
        for uid_str in id_matches:
            uid = int(uid_str)
            res_id, res_name = restore_user_by_id(uid)
            if res_id:
                restored_list.append((res_id, res_name))
                try:
                    from user_panel import main_menu
                    bot.send_message(res_id, "✅ *تم استرجاع وتفعيل حسابك بنجاح من قِبل المشرف.*", reply_markup=main_menu(res_id), parse_mode="Markdown")
                except Exception:
                    pass

        if restored_list:
            resp = f"♻️ **تم استرجاع {len(restored_list)} حساب بنجاح عبر ID:**\n\n"
            for u_id, u_name in restored_list:
                resp += f"• 👤 {esc(u_name)} (ID: `{u_id}`)\n"
            bot.send_message(chat_id, resp, parse_mode="Markdown", reply_markup=admin_main_menu())
        else:
            bot.send_message(chat_id, "❌ لم يتم العثور على أي حساب بهذه الـ IDs في قائمة المحذوفين.", reply_markup=admin_main_menu())
        return

    with db_lock:
        cursor.execute("SELECT user_id, full_name FROM deleted_users WHERE full_name LIKE ?", (f"%{query}%",))
        matched = cursor.fetchall()
    if matched:
        for uid, name in matched:
            restore_user_by_id(uid)
            restored_list.append((uid, name))
            try:
                from user_panel import main_menu
                bot.send_message(uid, "✅ *تم استرجاع وتفعيل حسابك بنجاح.*", reply_markup=main_menu(uid), parse_mode="Markdown")
            except Exception:
                pass
        resp = f"♻️ **تم العثور على واسترجاع {len(restored_list)} حساب بالاسم ({query}):**\n\n"
        for u_id, u_name in restored_list:
            resp += f"• 👤 {esc(u_name)} (ID: `{u_id}`)\n"
        bot.send_message(chat_id, resp, parse_mode="Markdown", reply_markup=admin_main_menu())
    else:
        bot.send_message(chat_id, f"❌ لم يتم العثور على أي حساب محذوف بالاسم ({query}).", reply_markup=admin_main_menu())


# 3.5. مراسلة جماعية لمدرسة معينة
def process_school_broadcast(message, bot, school_name):
    import time
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    with db_lock:
        cursor.execute("SELECT user_id FROM users WHERE school LIKE ?", (f"{school_name}%",))
        uids = [r[0] for r in cursor.fetchall()]

    success, failed = 0, 0
    for uid in uids:
        try:
            bot.copy_message(chat_id=uid, from_chat_id=chat_id, message_id=message.message_id, protect_content=True)
            success += 1
        except Exception:
            failed += 1
        time.sleep(0.05)

    bot.send_message(
        chat_id,
        f"✅ **تم إرسال الرسالة لتلاميذ مدرسة ({esc(school_name)}):**\n"
        f"• نجح: `{success}` تلميذ\n• فشل: `{failed}`",
        reply_markup=admin_main_menu(),
        parse_mode="Markdown"
    )


# 4. إرسال رد في الشات للتلميذ
def process_send_chat_reply(message, bot, student_id, student_name):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    reply_text = (message.text or "").strip()
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        bot.send_message(
            student_id,
            f"👑 **رسالة جديدة من الأستاذ بلعقاب أسامة:**\n\n{reply_text}\n\n🕒 {now_time}",
            parse_mode="Markdown"
        )
        with db_lock:
            cursor.execute(
                "INSERT INTO chat_messages (sender_id, sender_name, receiver_id, message_text, created_at, is_read) VALUES (?, ?, ?, ?, ?, 1)",
                (ADMIN_ID, "الأستاذ أسامة", student_id, reply_text, now_time)
            )
            conn.commit()
        bot.send_message(chat_id, f"✅ تم إرسال ردك بنجاح إلى التلميذ ({student_name})!", reply_markup=admin_reply_menu())
        send_admin_panel(bot, chat_id)
    except Exception as e:
        bot.send_message(chat_id, f"❌ تعذر إرسال الرد:\n`{e}`", reply_markup=admin_reply_menu(), parse_mode="Markdown")


# 4. حفظ العلامة والملاحظات الاحترافية
def process_save_grade(message, bot, student_id, ex_id):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    grade_val = (message.text or "").strip()
    with db_lock:
        cursor.execute("SELECT name FROM exercises WHERE id = ?", (ex_id,))
        res = cursor.fetchone()
    ex_name = res[0] if res else "الواجب"
    clean_ex_name = ex_name.replace("حل", "").strip()

    now_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    day_names = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    day_name = day_names[datetime.now().weekday()]
    full_date = f"{day_name} {now_time}"

    try:
        with db_lock:
            cursor.execute(
                "UPDATE submissions SET has_grade = 1, grade_text = ? WHERE student_id = ? AND exercise_name = ?",
                (grade_val, student_id, ex_name)
            )
            conn.commit()

        bot.send_message(
            student_id,
            f"🎯 **تم تدوين وتقييم علامة واجب ({ex_name}):**\n\n"
            f"⭐ **العلامة الممنوحة:** *{grade_val}*\n"
            f"🗓️ **تاريخ التصحيح:** {full_date}\n\n"
            "يمكنك مراجعة تفاصيل التقييم من قسم 📊 علامتي في الواجبات.",
            parse_mode="Markdown"
        )
        bot.send_message(chat_id, f"✅ تم حفظ وإرسال العلامة ({grade_val}) بنجاح للتلميذ!", reply_markup=admin_reply_menu())
        send_admin_panel(bot, chat_id)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطأ: {e}", reply_markup=admin_reply_menu())


def process_save_note(message, bot, student_id, ex_id):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    new_note = (message.text or "").strip()
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    day_names = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    day_name = day_names[datetime.now().weekday()]
    full_date = f"{day_name} {now_time}"

    with db_lock:
        cursor.execute("SELECT name FROM exercises WHERE id = ?", (ex_id,))
        res = cursor.fetchone()
    ex_name = res[0] if res else "الواجب"
    clean_ex_name = ex_name.replace("حل", "").strip()

    with db_lock:
        cursor.execute("SELECT notes_history FROM submissions WHERE student_id = ? AND exercise_name = ?",
                       (student_id, ex_name))
        row = cursor.fetchone()
    curr_notes = row[0] if (row and row[0]) else ""
    updated_notes = (curr_notes + f"\n• [{full_date}] الأستاذ: {new_note}").strip()

    try:
        with db_lock:
            cursor.execute(
                "UPDATE submissions SET notes_history = ? WHERE student_id = ? AND exercise_name = ?",
                (updated_notes, student_id, ex_name)
            )
            conn.commit()

        bot.send_message(
            student_id,
            f"📝 **توجيه وملاحظة من الأستاذ حول واجب ({ex_name}):**\n\n"
            f"💬 *الملاحظة:*\n_{new_note}_\n\n"
            f"🗓️ {full_date}",
            parse_mode="Markdown"
        )
        bot.send_message(chat_id, "✅ تم تسجيل وإرسال الملاحظة بنجاح للتلميذ!", reply_markup=admin_reply_menu())
        send_admin_panel(bot, chat_id)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطأ: {e}", reply_markup=admin_reply_menu())


# 5. مراسلة بـ ID مع رسالة تأكيد
def process_get_user_id_for_msg(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    input_id = (message.text or "").strip()
    if not input_id.isdigit():
        bot.send_message(chat_id, "❌ يرجى إدخال ID صحيح (أرقام فقط).")
        return
    global target_user_id
    target_user_id = int(input_id)
    name = get_user_full_name(target_user_id)
    msg = bot.send_message(chat_id, f"📝 **أرسل الآن الرسالة (نص، صورة، أو ملف) الموجهة لـ ({name}):**", reply_markup=admin_reply_menu())
    bot.register_next_step_handler(msg, prepare_direct_message, bot)


def prepare_direct_message(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    global direct_message_id
    direct_message_id = message.message_id
    name = get_user_full_name(target_user_id)

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ تأكيد الإرسال", callback_data="confirm_direct"),
        InlineKeyboardButton("❌ إلغاء", callback_data="admin_home")
    )
    bot.copy_message(chat_id=chat_id, from_chat_id=chat_id, message_id=message.message_id)
    bot.send_message(
        chat_id,
        f"👆 **معاينة الرسالة أعلاه.**\nهل تؤكد إرسالها إلى التلميذ: *{esc(name)}* (ID: `{target_user_id}`)؟",
        reply_markup=markup,
        parse_mode="Markdown"
    )


# 6. البث العام Broadcast مع رسالة تأكيد
def prepare_broadcast(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    global broadcast_message_id
    broadcast_message_id = message.message_id
    total_students = db.get_total_active_students()

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ تأكيد الإذاعة لجميع التلاميذ", callback_data="confirm_broadcast"),
        InlineKeyboardButton("❌ إلغاء", callback_data="admin_home")
    )
    bot.copy_message(chat_id=chat_id, from_chat_id=chat_id, message_id=message.message_id)
    bot.send_message(
        chat_id,
        f"👆 **معاينة الإعلان أعلاه.**\nهل تؤكد إرسال هذا الإعلان الآن لجميع التلاميذ المفعلين (إجمالي: `{total_students}` تلميذ)؟",
        reply_markup=markup,
        parse_mode="Markdown"
    )


def start_broadcasting(bot, chat_id, target_msg_id):
    global is_broadcasting
    users = get_users_list()
    is_broadcasting = True
    success, failed = 0, 0
    stop_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🛑 إيقاف البث", callback_data="stop_broadcast"))
    status_msg = bot.send_message(chat_id, "⏳ جاري بث الإعلان لجميع التلاميذ...", reply_markup=stop_markup)

    for user in users:
        if not is_broadcasting:
            break
        try:
            bot.copy_message(chat_id=user[0], from_chat_id=chat_id, message_id=target_msg_id)
            success += 1
        except Exception:
            failed += 1
        time.sleep(0.04)

    status_text = "🛑 **تم إيقاف الإذاعة!**\n\n" if not is_broadcasting else "📊 **تمت الإذاعة بنجاح:**\n\n"
    try:
        bot.edit_message_text(
            f"{status_text}✅ تم بنجاح: `{success}` تلميذ\n❌ تعذر الإرسال: `{failed}`",
            chat_id=chat_id,
            message_id=status_msg.message_id,
            parse_mode="Markdown"
        )
    except Exception:
        pass


# 7. إضافة مواضيع البكالوريا
def process_add_bac_stream(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    stream = (message.text or "").strip()
    admin_state[chat_id] = {"bac_stream": stream}
    msg = bot.send_message(chat_id, "🗓️ **أدخل سنة موضوع البكالوريا (مثال: 2024 أو 2023):**", reply_markup=admin_reply_menu())
    bot.register_next_step_handler(msg, process_add_bac_year, bot)


def process_add_bac_year(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    year_text = (message.text or "").strip()
    if not year_text.isdigit():
        retry = bot.send_message(chat_id, "❌ يرجى إدخال سنة صحيحة بالأرقام:", reply_markup=admin_reply_menu())
        bot.register_next_step_handler(retry, process_add_bac_year, bot)
        return
    admin_state.setdefault(chat_id, {})["bac_year"] = int(year_text)
    msg = bot.send_message(chat_id, "📝 **أدخل عنوان الموضوع (مثال: بكالوريا 2024 - الدورة العادية + الحل):**", reply_markup=admin_reply_menu())
    bot.register_next_step_handler(msg, process_add_bac_title, bot)


def process_add_bac_title(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    admin_state.setdefault(chat_id, {})["bac_title"] = (message.text or "").strip()
    msg = bot.send_message(chat_id, "📄 **أرسل الآن ملف PDF الخاص بموضوع البكالوريا:**", reply_markup=admin_reply_menu())
    bot.register_next_step_handler(msg, process_add_bac_file, bot)


def process_add_bac_file(message, bot):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    if not message.document:
        msg = bot.send_message(chat_id, "❌ يرجى إرسال ملف PDF صالح:")
        bot.register_next_step_handler(msg, process_add_bac_file, bot)
        return

    file_id = message.document.file_id
    info = admin_state.pop(chat_id, {})
    stream = info.get("bac_stream", "عام")
    year = info.get("bac_year", 2024)
    title = info.get("bac_title", "موضوع بكالوريا")
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    with db_lock:
        cursor.execute(
            "INSERT INTO bac_topics (year, stream, title, pdf_file_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (year, stream, title, file_id, now_time)
        )
        conn.commit()

    bot.send_message(chat_id, f"✅ تمت إضافة موضوع البكالوريا ({title}) بنجاح!", reply_markup=admin_reply_menu())
    send_admin_panel(bot, chat_id)

    if get_setting("notify_bac", "1") == "1":
        notify_all_students(bot, f"🎓 **تمت إضافة موضوع بكالوريا جديد:**\n\n• الشعبة: *{esc(stream)}*\n• السنة: *{year}*\n• الموضوع: *{esc(title)}*\n\n📂 يمكنك تحميله الآن من قسم مواضيع BAC.")


# 8. إدارة الملفات المتنوعة
def process_create_folder(message, bot, parent_id):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    folder_name = (message.text or "").strip()
    with db_lock:
        cursor.execute("INSERT INTO file_folders (name, parent_id) VALUES (?, ?)", (folder_name, parent_id))
        conn.commit()
    bot.send_message(chat_id, f"✅ تم إنشاء المجلد ({folder_name}) بنجاح!", reply_markup=admin_reply_menu())
    send_admin_panel(bot, chat_id)


def process_upload_file_title(message, bot, folder_id):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return
    title = (message.text or "").strip()
    msg = bot.send_message(chat_id, "📄 **أرسل الآن ملف PDF أو الصورة:**", reply_markup=admin_reply_menu())
    bot.register_next_step_handler(msg, process_upload_file_data, bot, folder_id, title)


def process_upload_file_data(message, bot, folder_id, title):
    chat_id = message.chat.id
    if message.text == "القائمة الرئيسية":
        send_admin_panel(bot, chat_id)
        return

    if message.document:
        f_id = message.document.file_id
        f_type = "document"
    elif message.photo:
        f_id = message.photo[-1].file_id
        f_type = "photo"
    else:
        msg = bot.send_message(chat_id, "❌ يرجى إرسال ملف PDF أو صورة صالحة:")
        bot.register_next_step_handler(msg, process_upload_file_data, bot, folder_id, title)
        return

    with db_lock:
        cursor.execute("INSERT INTO custom_files (title, file_id, file_type, folder_id) VALUES (?, ?, ?, ?)",
                       (title, f_id, f_type, folder_id))
        conn.commit()
    bot.send_message(chat_id, f"✅ تم حفظ الملف ({title}) بنجاح!", reply_markup=admin_reply_menu())
    send_admin_panel(bot, chat_id)


def notify_all_students(bot, text):
    """يرسل الإشعار في خيط منفصل حتى لا يتجمد البوت أثناء الإرسال لعدد كبير من التلاميذ."""
    users = get_users_list()

    def _run():
        for u in users:
            try:
                bot.send_message(u[0], text, parse_mode="Markdown")
            except Exception:
                pass
            time.sleep(0.04)

    threading.Thread(target=_run, daemon=True).start()

# ===================== دوال واجهات إدارة الفيديوهات =====================
def build_admin_videos_menu():
    videos = db.get_all_videos()
    total = len(videos)
    active_count = sum(1 for v in videos if v[5] == 1)

    text = (
        f"🎥 **إدارة الفيديوهات والشروحات الرسمية:**\n\n"
        f"📊 **إجمالي الفيديوهات:** {total} | 🟢 **المفعلة:** {active_count} | 🔴 **المغلقة:** {total - active_count}\n\n"
        f"💡 يمكنك إضافة فيديو جديد، تفعيله أو إغلاقه بضغطة واحدة، أو حذفه من المنصة والبوت:"
    )

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("➕ إضافة فيديو جديد", callback_data="add_new_video_start")
    )

    for v in videos:
        vid_id, title, yt_id, unit_id, unit_name, is_act, created = v
        status_icon = "🟢" if is_act == 1 else "🔴"
        toggle_text = "🔒 إغلاق" if is_act == 1 else "🔓 تفعيل"
        yt_link = f"https://youtu.be/{yt_id}" if yt_id else "https://youtube.com"

        title_display = f"{status_icon} {title[:32]} ({unit_name})"
        markup.add(InlineKeyboardButton(title_display, url=yt_link))
        markup.add(
            InlineKeyboardButton(f"🔄 {toggle_text}", callback_data=f"toggle_vid_{vid_id}"),
            InlineKeyboardButton("🗑️ حذف", callback_data=f"del_vid_{vid_id}")
        )

    markup.add(InlineKeyboardButton("🔙 العودة للوحة الأدمن", callback_data="admin_home"))
    return text, markup


def build_video_units_picker():
    markup = InlineKeyboardMarkup(row_width=2)
    units = [
        (1, "الدوال العددية 📉"),
        (2, "الدلة الأسية 📈"),
        (3, "الدلة اللوغارتمية 📊"),
        (4, "المتتاليات 🔢"),
        (5, "الاحتمالات 🎲"),
        (6, "القسمة في Z ➗"),
        (7, "الأعداد المركبة 🧮"),
        (0, "شرح عام 🎬")
    ]
    for uid, uname in units:
        markup.add(InlineKeyboardButton(uname, callback_data=f"vid_u_{uid}"))
    markup.add(InlineKeyboardButton("🔙 إلغاء", callback_data="manage_videos_menu"))
    return markup

def find_student_code_info(query):
    """بحث شامل عن التلميذ ورمزه السري في قاعدة بيانات الموقع Turso وقاعدة البوت."""
    query = str(query).strip()
    results = []
    # 1. البحث في الموقع (Turso)
    try:
        from web_server import turso_client
        if turso_client:
            web_rows = turso_client.execute("""
                SELECT id, first_name, last_name, phone, school, branch, code, active, created_at
                FROM web_users
                WHERE phone LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR code = ?
            """, [f"%{query}%", f"%{query}%", f"%{query}%", query.upper()])
            for r in web_rows:
                act = int(r[7] or 0)
                status_str = "✅ مفعّل ونشط" if act == 1 else ("🚫 محظور / مطرود" if act == -1 else "⏳ قيد الانتظار")
                results.append({
                    "id": r[0],
                    "name": f"{r[1]} {r[2]}".strip(),
                    "phone": r[3],
                    "school": r[4],
                    "branch": r[5],
                    "code": r[6],
                    "status": status_str,
                    "active": act,
                    "created_at": r[8],
                    "source_type": "web"
                })
    except Exception as e:
        print("Search web_users error:", e)

    # 2. البحث في البوت (SQLite / Users)
    with db_lock:
        try:
            cursor.execute("""
                SELECT user_id, full_name, school, last_active, joined_at, phone, code, source
                FROM users
                WHERE CAST(user_id AS TEXT) = ? OR full_name LIKE ? OR phone LIKE ? OR code = ?
            """, (query, f"%{query}%", f"%{query}%", query.upper()))
            for r in cursor.fetchall():
                p = r[5] if len(r) > 5 else ""
                c = r[6] if len(r) > 6 else ""
                src = r[7] if len(r) > 7 and r[7] else "bot"
                if p and any(res.get("phone") == p for res in results):
                    continue
                results.append({
                    "id": r[0],
                    "name": r[1],
                    "phone": p or "غير مسجل",
                    "school": r[2],
                    "branch": "—",
                    "code": c or "—",
                    "status": "✅ مفعّل في البوت",
                    "active": 1,
                    "created_at": r[4],
                    "source_type": src
                })

            cursor.execute("""
                SELECT user_id, full_name, school, code, created_at, phone, source
                FROM wait
                WHERE CAST(user_id AS TEXT) = ? OR full_name LIKE ? OR phone LIKE ? OR code = ?
            """, (query, f"%{query}%", f"%{query}%", query.upper()))
            for r in cursor.fetchall():
                p = r[5] if len(r) > 5 else ""
                c = r[3] if len(r) > 3 else ""
                src = r[6] if len(r) > 6 and r[6] else "bot"
                if p and any(res.get("phone") == p for res in results):
                    continue
                results.append({
                    "id": r[0],
                    "name": r[1],
                    "phone": p or "غير مسجل",
                    "school": r[2],
                    "branch": "—",
                    "code": c or "—",
                    "status": "⏳ انتظار تفعيل في البوت",
                    "active": 0,
                    "created_at": r[4],
                    "source_type": src
                })
        except Exception as e:
            print("Search local error:", e)

    return results


def process_find_student_code(message, bot):
    """معالج إدخال الأستاذ للبحث عن الرمز السري."""
    chat_id = message.chat.id
    query = (message.text or "").strip()
    if query in ["/start", "القائمة الرئيسية"]:
        reset_admin_state(chat_id)
        send_admin_panel(bot, chat_id)
        return

    results = find_student_code_info(query)
    if not results:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔍 محاولة بحث أخرى", callback_data="find_student_code"))
        markup.add(InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="admin_home"))
        bot.send_message(
            chat_id,
            f"❌ لم يتم العثور على أي تلميذ يطابق: `{query}`\n\nتأكد من كتابة رقم الهاتف (10 أرقام) أو الاسم بشكل صحيح.",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return

    if len(results) == 1:
        text, markup = build_student_info_card(results[0])
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
    else:
        # أكثر من تلميذ يطابق البحث
        markup = InlineKeyboardMarkup()
        text = f"🔎 **تم العثور على {len(results)} نتائج مطابقة:**\n\n"
        for st in results[:10]:
            src_b = "🌐 موقع" if st["source_type"] == "web" else "🤖 بوت"
            text += f"👤 *{esc(st['name'])}* | 📱 `{st['phone']}` | 🔑 `{st['code']}` | 🏷️ {src_b}\n"
            text += f"الحالة: {st['status']}\n──────────────────\n"
        markup.add(InlineKeyboardButton("🔍 بحث جديد", callback_data="find_student_code"))
        markup.add(InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="admin_home"))
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

def get_all_students_for_codes():
    """جلب جميع التلاميذ من الموقع والبوت لعرض أرقامهم ورموزهم السرية."""
    all_st = []
    seen = set()

    # 1. تلاميذ الموقع من Turso
    try:
        from web_server import turso_client
        if turso_client:
            web_rows = turso_client.execute("""
                SELECT id, first_name, last_name, phone, school, branch, code, active
                FROM web_users
                ORDER BY id DESC
            """)
            for r in web_rows:
                ph = r[3] or "غير مسجل"
                act = int(r[7] or 0)
                status_icon = "🟢 مفعل" if act == 1 else ("🔴 محظور" if act == -1 else "🟡 انتظار")
                all_st.append({
                    "id": f"W-{r[0]}",
                    "name": f"{r[1]} {r[2]}".strip(),
                    "phone": ph,
                    "code": r[6] or "—",
                    "school": r[4] or "دراسة عن بعد 🌐",
                    "branch": r[5] or "",
                    "source": "🌐 الموقع",
                    "status": status_icon
                })
                if ph != "غير مسجل":
                    seen.add(ph)
    except Exception as e:
        print("get_all_students_for_codes Turso error:", e)

    # 2. تلاميذ البوت (users & wait)
    with db_lock:
        try:
            cursor.execute("SELECT user_id, full_name, school, phone, code FROM users ORDER BY user_id DESC")
            for r in cursor.fetchall():
                p = r[3] if len(r) > 3 and r[3] else "غير مسجل"
                if p != "غير مسجل" and p in seen:
                    continue
                all_st.append({
                    "id": str(r[0]),
                    "name": r[1],
                    "phone": p,
                    "code": r[4] if len(r) > 4 and r[4] else "—",
                    "school": r[2],
                    "branch": "",
                    "source": "🤖 البوت",
                    "status": "🟢 مفعل"
                })
                if p != "غير مسجل":
                    seen.add(p)

            cursor.execute("SELECT user_id, full_name, school, phone, code, source FROM wait ORDER BY user_id DESC")
            for r in cursor.fetchall():
                p = r[3] if len(r) > 3 and r[3] else "غير مسجل"
                if p != "غير مسجل" and p in seen:
                    continue
                all_st.append({
                    "id": str(r[0]),
                    "name": r[1],
                    "phone": p,
                    "code": r[4] if len(r) > 4 and r[4] else "—",
                    "school": r[2],
                    "branch": "",
                    "source": "🤖 البوت" if (len(r) > 5 and r[5] != "web") else "🌐 الموقع",
                    "status": "🟡 انتظار"
                })
        except Exception as e:
            print("get_all_students_for_codes local error:", e)

    return all_st


def build_secret_codes_page(page=0, page_size=8):
    """بناء واجهة قائمة الأرقام والرموز السرية لكل التلاميذ مع التصفح بالصفحات."""
    students = get_all_students_for_codes()
    total = len(students)
    start = page * page_size
    end = start + page_size
    current_page_items = students[start:end]
    total_pages = max(1, (total + page_size - 1) // page_size)

    text = f"🔑 **قائمة الأرقام والرموز السرية للتلاميذ:**\n"
    text += f"📊 إجمالي التلاميذ: **{total}** | الصفحة: **{page + 1} من {total_pages}**\n"
    text += f"──────────────────\n\n"

    if not current_page_items:
        text += "🚫 لا توجد بيانات تلاميذ حالياً.\n"
    else:
        for idx, s in enumerate(current_page_items, start=start + 1):
            sch_text = s['school']
            if s.get('branch'):
                sch_text += f" ({s['branch']})"
            text += f"{idx}. 👤 *{esc(s['name'])}* | {s['status']} | {s['source']}\n"
            text += f"   📱 الهاتف: `{s['phone']}`\n"
            text += f"   🔑 الرمز السري: `{s['code']}`\n"
            text += f"   🏫 {esc(sch_text)} | 🆔 `{s['id']}`\n"
            text += f"──────────────────\n"

    markup = InlineKeyboardMarkup()
    # زر البحث المخصص بالاسم أو ID أو الهاتف
    markup.add(
        InlineKeyboardButton("🔍 بحث بالاسم واللقب أو ID أو الهاتف", callback_data="find_student_code")
    )

    # أزرار التنقل بين الصفحات
    nav_btns = []
    if page > 0:
        nav_btns.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"secret_codes_menu_{page - 1}"))
    nav_btns.append(InlineKeyboardButton(f"📄 {page + 1} / {total_pages}", callback_data="ignore_click"))
    if end < total:
        nav_btns.append(InlineKeyboardButton("التالي ➡️", callback_data=f"secret_codes_menu_{page + 1}"))
    if nav_btns:
        markup.row(*nav_btns)

    markup.add(InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="admin_home"))
    return text, markup
