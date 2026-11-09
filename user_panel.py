"""لوحة تحكم التلميذ الكاملة للوصول إلى الدروس، التمارين، الواجبات، البكالوريا، والتواصل."""
import os
import json
from datetime import datetime

from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, ReplyKeyboardRemove
)

import db
from db import conn, cursor, get_user, user_steps, get_setting, db_lock
from utils import send_media_bundle, parse_media, throttle, first_in_album

ADMIN_ID = 5279737033

# كاش مؤقت لإرسال حلول الواجبات
student_temp_data = {}
student_photos_cache = {}
student_last_unit = {}  # آخر وحدة فتحها التلميذ (لتفادي تشابه أسماء التمارين بين الوحدات)


def _md(s):
    """هروب رموز Markdown لتفادي انكسار النصوص."""
    s = "" if s is None else str(s)
    for ch in ("\\", "_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


esc = _md


def _clear_student_state(chat_id):
    student_temp_data.pop(chat_id, None)
    student_photos_cache.pop(chat_id, None)


# ===================== القوائم الرئيسية للتلميذ =====================
def main_menu(user_id=None):
    active_hw = db.get_active_homework_count()
    hw_label = f"📤 إرسال محاولة حل واجب ({active_hw})" if active_hw > 0 else "📤 إرسال محاولة حل واجب"

    unread_chat = db.get_unread_chat_count(user_id) if (user_id and user_id != ADMIN_ID) else 0
    chat_label = f"💬 تواصل مع الأستاذ (🔴 {unread_chat})" if unread_chat > 0 else "💬 تواصل مع الأستاذ"

    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("📘 الدروس"), KeyboardButton("📝 التمارين والحلول"))
    markup.add(KeyboardButton("🎥 الفيديوهات الشارحة"), KeyboardButton(hw_label))
    markup.add(KeyboardButton("📊 علامتي في الواجبات"), KeyboardButton("📂 مواضيع BAC"))
    markup.add(KeyboardButton("📁 ملفات متنوعة"), KeyboardButton(chat_label))
    markup.add(KeyboardButton("🤖 البوت الآلي لحل التمارين"), KeyboardButton("ℹ️ مساعدة"))
    if user_id == ADMIN_ID and ADMIN_ID in db.admin_preview:
        markup.add(KeyboardButton("🔙 العودة للوحة الأدمن"))
    return markup


# ------------------- قوائم الواجبات المفعلة -------------------
def submit_solution_units_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    with db_lock:
        cursor.execute("""
            SELECT u.id, u.name, COUNT(e.id)
            FROM units u
            JOIN exercises e ON u.id = e.unit_id AND e.is_active = 1 AND e.item_type = 'homework'
            WHERE u.is_active = 1
            GROUP BY u.id
        """)
        units_data = cursor.fetchall()

    has_items = False
    for u_id, u_name, count in units_data:
        if count > 0:
            has_items = True
            clean_unit = u_name.replace("📉", "").replace("📈", "").replace("📊", "").replace("🔢", "").replace("🎲", "").replace("➗", "").replace("🧮", "").strip()
            markup.add(KeyboardButton(f"وجبات {clean_unit} ({count} واجبات)"))

    if not has_items:
        markup.add(KeyboardButton("⚠️ لا توجد واجبات مفعلة حالياً"))

    markup.add(KeyboardButton("⬅️ رجوع"))
    return markup


def submit_solution_exercises_menu(unit_text):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    clean_name = unit_text.split("(")[0].replace("وجبات", "").strip()

    with db_lock:
        cursor.execute("""
            SELECT e.id, e.name FROM exercises e
            JOIN units u ON e.unit_id = u.id
            WHERE u.name LIKE ? AND e.is_active = 1 AND e.item_type = 'homework' AND u.is_active = 1
            ORDER BY e.id ASC
        """, (f"%{clean_name}%",))
        exercises = cursor.fetchall()

    for ex_id, ex_name in exercises:
        markup.add(KeyboardButton(f"حل {ex_name}"))

    markup.add(KeyboardButton("⬅️ رجوع إلى الوحدات"))
    return markup


# ------------------- قوائم الوحدات للدروس والتمارين -------------------
def units_menu(target_type="lesson"):
    with db_lock:
        cursor.execute("SELECT id, name FROM units WHERE is_active = 1 ORDER BY id ASC")
        units = cursor.fetchall()

    prefix = "دروس" if target_type == "lesson" else "تمارين"
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    row = []
    for u_id, u_name in units:
        # نبقي اسم الوحدة كاملاً مع إيموجيه
        row.append(KeyboardButton(f"{prefix} {u_name}"))
        if len(row) == 2:
            markup.add(*row)
            row = []
    if row:
        markup.add(*row)
    markup.add(KeyboardButton("⬅️ رجوع"))
    return markup


# ===================== تسجيل المعالجات (Handlers) =====================
def register_user_handlers(bot):

    # في وضع معاينة الأدمن: أضف زر الخروج تلقائياً إلى كل لوحة أزرار سفلية
    EXIT_BTN = "🔙 العودة للوحة الأدمن"
    _orig_send_message = bot.send_message

    def _send_message_with_exit(chat_id, text, *args, **kwargs):
        rm = kwargs.get("reply_markup")
        if (chat_id == ADMIN_ID and ADMIN_ID in db.admin_preview
                and isinstance(rm, ReplyKeyboardMarkup)
                and EXIT_BTN not in str(rm.keyboard)):
            rm.add(KeyboardButton(EXIT_BTN))
        return _orig_send_message(chat_id, text, *args, **kwargs)

    bot.send_message = _send_message_with_exit

    # حارس: منع غير المسجلين
    @bot.message_handler(
        func=lambda m: m.from_user.id != ADMIN_ID
        and not get_user(m.from_user.id)
        and m.from_user.id not in user_steps,
        content_types=['text', 'photo', 'document', 'video', 'video_note', 'audio', 'voice'])
    def guard(message):
        bot.send_message(message.chat.id, "🔐 اكتب /start للتسجيل أولاً.")


    # 🎥 الفيديوهات الشارحة الرسمية
    @bot.message_handler(func=lambda message: message.text in ["🎥 الفيديوهات الشارحة", "🎥 الفيديوهات", "الفيديوهات"])
    def show_videos_handler(message):
        chat_id = message.chat.id
        vids = db.get_active_videos()
        if not vids:
            bot.send_message(
                chat_id,
                "🎥 **الفيديوهات والشروحات الرسمية:**\n\n"
                "لا توجد شروحات فيديو منشورة حالياً في هذه اللحظة، سيتم نشر فيديوهات جديدة قريباً من طرف الأستاذ أسامة! 🚀",
                reply_markup=main_menu(message.from_user.id),
                parse_mode="Markdown"
            )
            return

        markup = InlineKeyboardMarkup(row_width=1)
        for v in vids[:20]:
            vid_id, title, yt_id, unit_id, unit_name, is_act, created = v
            yt_link = f"https://youtu.be/{yt_id}" if yt_id else "https://youtube.com"
            btn_text = f"🎬 {title[:40]} ({unit_name})"
            markup.add(InlineKeyboardButton(btn_text, url=yt_link))

        bot.send_message(
            chat_id,
            "🎥 **شروحات وفيديوهات الأستاذ بلعقاب أسامة:**\n\n"
            "اضغط على أي فيديو في القائمة أدناه لمشاهدته مباشرة على YouTube بجودة عالية 🔥👇",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    # 1. البوت الآلي لحل التمارين
    @bot.message_handler(func=lambda message: message.text == "🤖 البوت الآلي لحل التمارين")
    def ai_solver_bot(message):
        bot.send_message(
            message.chat.id,
            "🤖 **المساعد الذكي لحل التمارين:**\n\n"
            "رانا نخدمو على تطوير ميزة الذكاء الاصطناعي باش تولي تحط التمرين يحلّولك خطوة بخطوة ويفهمك بالديطاي.. قريباً تكون واجدة إن شاء الله!\n\n"
            "✨ ترقبوا التحديث القادم..",
            parse_mode="Markdown"
        )

    # 2. تواصل مع الأستاذ (نظام محادثة شبيه بإنستغرام + حذف تلقائي بعد 24 ساعة + خيار الحذف)
    @bot.message_handler(func=lambda message: message.text and message.text.startswith("💬 تواصل مع الأستاذ"))
    def contact_teacher_start(message):
        chat_id = message.chat.id
        db.cleanup_old_chat_messages()

        if get_setting("chat_enabled", "1") != "1":
            bot.send_message(
                chat_id,
                "🚫 **محادثة الأستاذ أسامة المباشرة مغلقة حالياً.**\n"
                "سيتم فتحها في الأوقات المخصصة للإجابة عن أسئلتكم واستفساراتكم.",
                parse_mode="Markdown"
            )
            return

        with db_lock:
            # وضع الرسائل كمقروءة من طرف التلميذ
            cursor.execute("UPDATE chat_messages SET is_read = 1 WHERE receiver_id = ? AND sender_id = ?", (chat_id, ADMIN_ID))
            conn.commit()
            cursor.execute("""
                SELECT id, sender_id, sender_name, message_text, created_at
                FROM chat_messages
                WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)
                ORDER BY id ASC
            """, (chat_id, ADMIN_ID, ADMIN_ID, chat_id))
            messages = cursor.fetchall()

        header = (
            "💬 **المحادثة المباشرة مع الأستاذ بلعقاب أسامة:**\n"
            "⚠️ *تُحذف الرسائل تلقائياً بعد مرور 24 ساعة من القراءة.*\n"
            "──────────────────────────────\n\n"
        )
        text = ""

        if not messages:
            text += (
                "📭 **لا توجد أي رسائل سابقة في آخر 24 ساعة.**\n"
                "عندك سؤال ولا تمرين حاب تستفسر عليه؟ اضغط على الزر أدناه وابعث رسالتك للأستاذ أسامة 👇\n\n"
            )
        else:
            blocks = []
            for m_id, s_id, s_name, msg_text, created_at in messages:
                short_time = created_at.split()[-1][:5] if created_at else ""
                body = _md(msg_text)
                if len(body) > 700:
                    body = body[:700] + "…"
                if s_id == chat_id:
                    # رسالة التلميذ (المرسل: أنت ➔ إلى اليمين بفقاعة أنيقة)
                    blocks.append(
                        f"                                        👤 **أنت** 🕒 `{short_time}`\n"
                        f"                             ╭──────────────────\n"
                        f"                             │ 💬 {body}\n"
                        f"                             ╰──────────────────\n\n"
                    )
                else:
                    # رسالة الأستاذ (الواردة: الأستاذ أسامة ➔ إلى اليسار)
                    blocks.append(
                        f"👑 **الأستاذ أسامة** 🕒 `{short_time}`\n"
                        f"╭──────────────────\n"
                        f"│ 📩 _{body}_\n"
                        f"╰──────────────────\n\n"
                    )
            # حد تيليجرام 4096 حرفاً: نُبقي الرسائل الأحدث فقط
            while len(blocks) > 1 and sum(len(b) for b in blocks) > 3200:
                blocks.pop(0)
            text += "".join(blocks)

        text = header + text + "──────────────────────────────"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✍️ إرسال رسالة جديدة للأستاذ أسامة", callback_data="student_send_new_msg"))
        markup.add(
            InlineKeyboardButton("🗑️ مسح المحادثة", callback_data="student_clear_chat"),
            InlineKeyboardButton("🔄 تحديث المحادثة", callback_data="student_refresh_chat")
        )

        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: call.data == "student_clear_chat")
    def student_clear_chat_cb(call):
        chat_id = call.message.chat.id
        db.delete_chat_history(chat_id)
        bot.answer_callback_query(call.id, "🗑️ تم مسح محادثتك بنجاح!")
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except Exception:
            pass
        contact_teacher_start(call.message)

    @bot.callback_query_handler(func=lambda call: call.data == "student_refresh_chat")
    def student_refresh_chat_cb(call):
        bot.answer_callback_query(call.id, "🔄 جاري التحديث...")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        contact_teacher_start(call.message)

    @bot.callback_query_handler(func=lambda call: call.data == "student_send_new_msg")
    def student_send_new_msg_cb(call):
        chat_id = call.message.chat.id
        msg = bot.send_message(
            chat_id,
            "💬 **اكتب رسالتك أو استفسارك الآن للأستاذ أسامة :**\n(تقدر تبعث نص، تصويرة، ولا ملف PDF.. بصح الفيديو ممنوع ⚠️)",
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("⬅️ إلغاء والرجوع"))
        )
        bot.register_next_step_handler(msg, process_student_message_to_teacher, bot)
        bot.answer_callback_query(call.id)

    def process_student_message_to_teacher(message, bot):
        chat_id = message.chat.id
        if chat_id == ADMIN_ID and ADMIN_ID in db.admin_preview:
            bot.send_message(chat_id, "👁️ وضع المعاينة: المراسلة معطّلة.", reply_markup=main_menu(chat_id))
            return
        if message.text == "⬅️ إلغاء والرجوع":
            bot.send_message(chat_id, "القائمة الرئيسية:", reply_markup=main_menu(chat_id))
            return

        if message.video or message.video_note:
            bot.send_message(
                chat_id,
                "❌ **لا لا، الفيديو ممنوع في البوت!**\nاكتب سؤالك عادي ولا صوّر كراستك صورة واضحة أو ابعث PDF.",
                reply_markup=main_menu(chat_id)
            )
            return

        row = get_user(chat_id)
        student_name = row[1] if row else message.from_user.first_name
        school = row[2] if row else "غير محدد"
        now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # تحديد محتوى الرسالة وحفظ file_id إن وجد
        if message.text:
            text_content = message.text
            file_id_saved = None
            file_type_saved = None
        elif message.photo:
            text_content = message.caption or "📷 صورة"
            file_id_saved = message.photo[-1].file_id
            file_type_saved = "photo"
        elif message.document:
            text_content = message.caption or f"📄 {message.document.file_name or 'ملف'}"
            file_id_saved = message.document.file_id
            file_type_saved = "document"
        else:
            text_content = message.caption or "رسالة"
            file_id_saved = None
            file_type_saved = None

        with db_lock:
            cursor.execute(
                "INSERT INTO chat_messages (sender_id, sender_name, receiver_id, message_text, created_at, is_read, file_id, file_type) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (chat_id, student_name, ADMIN_ID, text_content, now_time, file_id_saved, file_type_saved)
            )
            conn.commit()

        admin_markup = InlineKeyboardMarkup()
        admin_markup.add(InlineKeyboardButton(f"✉️ فتح المحادثة والرد على {student_name}", callback_data=f"open_chat_{chat_id}"))

        try:
            bot.send_message(
                ADMIN_ID,
                f"💬 **استفسار جديد من التلميذ:**\n"
                f"👤 *{_md(student_name)}* ({_md(school)})\n"
                f"🆔 الآيدي: `{chat_id}`\n\n"
                f"📝 *الرسالة:*\n{_md(text_content)}\n\n🕒 {now_time}",
                reply_markup=admin_markup,
                parse_mode="Markdown"
            )
            if message.photo:
                bot.send_photo(ADMIN_ID, message.photo[-1].file_id, protect_content=True)
            elif message.document:
                bot.send_document(ADMIN_ID, message.document.file_id, protect_content=True)
        except Exception as e:
            print("Error notifying teacher:", e)

        bot.send_message(
            chat_id,
            "✅ **صحيت! تم إرسال رسالتك بنجاح إلى الأستاذ أسامة.**\nسيصلك إشعار بالرد هنا فور اطلاعه عليه.",
            reply_markup=main_menu(chat_id),
            parse_mode="Markdown"
        )

    # 3. مواضيع شهادة البكالوريا (مرتبة من العام الكبير إلى الصغير)
    @bot.message_handler(func=lambda message: message.text == "📂 مواضيع BAC")
    def bac_archive_start(message):
        markup = InlineKeyboardMarkup(row_width=2)
        streams = ["علوم تجريبية", "تقني رياضي", "رياضيات"]
        for st in streams:
            markup.add(InlineKeyboardButton(f"🎓 شعبة {st}", callback_data=f"bac_st_{st}"))
        bot.send_message(
            message.chat.id,
            "📂 **حوليات ومواضيع شهادة البكالوريا الرسمية BAC:**\n"
            "مرتبة حسب السنوات من الأحدث إلى الأقدم مع الحلول النموذجية 🎓🔥\n\n"
            "خيّر شعبتك باش تبدا التحميل والمراجعة 👇",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("bac_st_"))
    def bac_stream_chosen(call):
        bot.answer_callback_query(call.id)
        stream = call.data.replace("bac_st_", "")
        with db_lock:
            # ترتيب السنوات تصاعدياً من الأكبر إلى الأصغر (2025 -> 2008)
            cursor.execute("SELECT DISTINCT year FROM bac_topics WHERE stream = ? ORDER BY year DESC", (stream,))
            years = cursor.fetchall()

        markup = InlineKeyboardMarkup(row_width=3)
        if not years:
            markup.add(InlineKeyboardButton("🔙 رجوع للشعب", callback_data="bac_back_streams"))
            bot.edit_message_text(f"🚫 ماكانش مواضيع بكالوريا مسجلة لشعبة ({stream}) حالياً.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
            return

        buttons = [InlineKeyboardButton(f"📅 {y[0]}", callback_data=f"bac_yr_{stream}_{y[0]}") for y in years]
        markup.add(*buttons)
        markup.add(InlineKeyboardButton("🔙 رجوع للشعب", callback_data="bac_back_streams"))

        bot.edit_message_text(
            f"🎓 **مواضيع بكالوريا شعبة:** {esc(stream)}\n"
            "مرتبة من العام الكبير إلى الصغير 🗓️\n"
            "خيّر السنة باش تتيليشارجي الموضوع والحل النموذجي :",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    @bot.callback_query_handler(func=lambda call: call.data == "bac_back_streams")
    def bac_back_streams(call):
        bot.answer_callback_query(call.id)
        markup = InlineKeyboardMarkup(row_width=2)
        streams = ["علوم تجريبية", "تقني رياضي", "رياضيات"]
        for st in streams:
            markup.add(InlineKeyboardButton(f"🎓 شعبة {st}", callback_data=f"bac_st_{st}"))
        bot.edit_message_text(
            "📂 **مواضيع شهادة البكالوريا BAC:**\nخيّر شعبتك :", 
            chat_id=call.message.chat.id, 
            message_id=call.message.message_id, 
            reply_markup=markup, 
            parse_mode="Markdown"
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("bac_yr_"))
    def bac_year_chosen(call):
        bot.answer_callback_query(call.id)
        parts = call.data.split("_")
        stream = parts[2]
        year = int(parts[3])

        with db_lock:
            cursor.execute("SELECT id, title, pdf_file_id FROM bac_topics WHERE stream = ? AND year = ? ORDER BY id DESC", (stream, year))
            topics = cursor.fetchall()

        markup = InlineKeyboardMarkup(row_width=1)
        for t_id, title, pdf_id in topics:
            markup.add(InlineKeyboardButton(f"📥 تحميل: {title}", callback_data=f"dl_bac_{t_id}"))
        markup.add(InlineKeyboardButton("🔙 رجوع للسنوات", callback_data=f"bac_st_{stream}"))

        bot.edit_message_text(
            f"🎓 **بكالوريا {year} - شعبة {esc(stream)}:**\nاضغط على الموضوع للتحميل المباشر بصيغة PDF:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("dl_bac_"))
    def bac_download_file(call):
        t_id = int(call.data.replace("dl_bac_", ""))
        if not throttle(("bac", call.message.chat.id, t_id), 3):
            bot.answer_callback_query(call.id)
            return
        with db_lock:
            cursor.execute("SELECT title, pdf_file_id FROM bac_topics WHERE id = ?", (t_id,))
            row = cursor.fetchone()
        if row and row[1]:
            try:
                bot.send_document(
                    call.message.chat.id,
                    row[1],
                    caption=f"📄 **{_md(row[0])}**",
                    parse_mode="Markdown",
                    protect_content=True
                )
            except Exception as e:
                bot.send_message(call.message.chat.id, f"❌ خطأ أثناء تحميل الملف: {e}")
        bot.answer_callback_query(call.id)

    # 4. تصفح الملفات المتنوعة
    @bot.message_handler(func=lambda message: message.text == "📁 ملفات متنوعة")
    def student_files_start(message):
        show_student_folder(message.chat.id, 0)

    def show_student_folder(chat_id, folder_id, msg_id=None):
        with db_lock:
            cursor.execute("SELECT id, name FROM file_folders WHERE parent_id = ? ORDER BY id ASC", (folder_id,))
            folders = cursor.fetchall()
            cursor.execute("SELECT id, title, file_type FROM custom_files WHERE folder_id = ? ORDER BY id ASC", (folder_id,))
            files = cursor.fetchall()

        markup = InlineKeyboardMarkup(row_width=1)
        if not folders and not files:
            text = "📁 لا توجد ملفات أو مجلدات متاحة هنا حالياً."
        else:
            text = "📁 **نظام الملفات المتنوعة:**\nاختر مجلداً للتصفح أو حمّل الملفات مباشرة:"

        for f_id, f_name in folders:
            markup.add(InlineKeyboardButton(f"📁 {f_name}", callback_data=f"stu_open_folder_{f_id}"))

        for fid, f_title, f_type in files:
            icon = "🖼️" if f_type == "photo" else "📄"
            markup.add(InlineKeyboardButton(f"{icon} {f_title}", callback_data=f"stu_dl_file_{fid}"))

        if folder_id != 0:
            markup.add(InlineKeyboardButton("🔙 رجوع للمجلد الرئيسي", callback_data="stu_open_folder_0"))

        if msg_id:
            bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("stu_open_folder_"))
    def stu_open_folder_cb(call):
        f_id = int(call.data.replace("stu_open_folder_", ""))
        show_student_folder(call.message.chat.id, f_id, call.message.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("stu_dl_file_"))
    def stu_dl_file_cb(call):
        fid = int(call.data.replace("stu_dl_file_", ""))
        if not throttle(("file", call.message.chat.id, fid), 3):
            bot.answer_callback_query(call.id)
            return
        with db_lock:
            cursor.execute("SELECT title, file_id, file_type FROM custom_files WHERE id = ?", (fid,))
            row = cursor.fetchone()
        if row:
            title, file_id, f_type = row
            try:
                if f_type == "photo":
                    bot.send_photo(call.message.chat.id, file_id, caption=f"🖼️ **{_md(title)}**", parse_mode="Markdown", protect_content=True)
                else:
                    bot.send_document(call.message.chat.id, file_id, caption=f"📄 **{_md(title)}**", parse_mode="Markdown", protect_content=True)
            except Exception as e:
                bot.send_message(call.message.chat.id, f"❌ حدث خطأ أثناء إرسال الملف: {e}")
        bot.answer_callback_query(call.id)

    # 5. عرض علامات الواجبات الاحترافي بالتاريخ والوقت
    @bot.message_handler(func=lambda message: message.text in ["📊 علامتي في الواجبات", "📊 علامتي في الوجبات"])
    def show_student_grades(message):
        student_id = message.chat.id
        with db_lock:
            cursor.execute("""
                SELECT s.exercise_name, MAX(s.has_grade), MAX(s.grade_text), MIN(s.id), COALESCE(MAX(u.name), 'أخرى')
                FROM submissions s
                LEFT JOIN exercises e ON s.exercise_name = e.name
                LEFT JOIN units u ON e.unit_id = u.id
                WHERE s.student_id = ?
                GROUP BY s.exercise_name
                ORDER BY MIN(s.id) DESC
            """, (student_id,))
            submissions = cursor.fetchall()

        if not submissions:
            bot.send_message(student_id, "⚠️ **لم يتم تسجيل أي واجبات أو علامات لك بعد.**", parse_mode="Markdown")
            return

        markup = InlineKeyboardMarkup()
        for ex_name, has_grade, grade_text, sub_id, unit_name in submissions:
            if has_grade == 1 and grade_text:
                grade_display = f"⭐ ({grade_text})"
            else:
                grade_display = "⏳ (قيد التصحيح)"
            clean_unit = unit_name.replace("📈", "").replace("🔢", "").replace("🎲", "").replace("➗", "").replace("🧮", "").strip()
            markup.add(InlineKeyboardButton(f"[{clean_unit}] {ex_name} : {grade_display}", callback_data=f"student_view_sub_{sub_id}"))

        bot.send_message(
            student_id,
            "📊 **كشف علامات الواجبات المنزلية:**\nاضغط على أي واجب للاطلاع على تفاصيل التقييم وتوجيهات الأستاذ:",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("student_view_sub_"))
    def handle_student_view_submission(call):
        chat_id = call.message.chat.id
        sub_id = int(call.data.replace("student_view_sub_", ""))

        with db_lock:
            cursor.execute("SELECT exercise_name FROM submissions WHERE id = ? AND student_id = ?", (sub_id, chat_id))
            name_row = cursor.fetchone()
        if not name_row:
            bot.answer_callback_query(call.id, "❌ لم يتم العثور على الواجب.")
            return
        ex_name = name_row[0]

        with db_lock:
            cursor.execute("""
                SELECT photo_id, has_grade, grade_text, notes_history, status
                FROM submissions
                WHERE student_id = ? AND exercise_name = ?
            """, (chat_id, ex_name))
            rows = cursor.fetchall()

        if not rows:
            bot.answer_callback_query(call.id, "❌ لا توجد بيانات مسجلة.")
            return

        has_grade = max(r[1] or 0 for r in rows)
        grade_text = next((r[2] for r in rows if r[2]), None)
        notes = next((r[3] for r in rows if r[3]), "لا توجد ملاحظات مسجلة بعد.")
        status_val = rows[0][4]
        photo_ids = [r[0] for r in rows if r[0] != "NO_SUBMISSION"]

        if status_val == "auto_zero":
            card = (
                f"══════════════════════════════\n"
                f"📝 **الواجب:** {_md(ex_name)}\n"
                f"⭐ **العلامة:** `00/20`\n"
                f"⚠️ **الحالة:** انتهت مهلة تسليم الواجب (تخلف عن التسليم).\n\n"
                f"💬 **ملاحظات النظام والأستاذ:**\n{_md(notes)}\n"
                f"══════════════════════════════"
            )
            bot.send_message(chat_id, card, parse_mode="Markdown")
            bot.answer_callback_query(call.id)
            return

        grade_badge = f"⭐ **العلامة الممنوحة:** `{grade_text}`" if (has_grade == 1 and grade_text) else "⏳ **الحالة:** قيد التدقيق والتصحيح من طرف الأستاذ"
        card = (
            f"══════════════════════════════\n"
            f"📝 **الواجب:** {_md(ex_name)}\n"
            f"{grade_badge}\n\n"
            f"💬 **ملاحظات وتوجيهات الأستاذ:**\n{_md(notes)}\n"
            f"══════════════════════════════"
        )

        if photo_ids:
            media_group = []
            for idx, p_id in enumerate(photo_ids[:10]):
                cap = card if idx == 0 else ""
                media_group.append(InputMediaPhoto(media=p_id, caption=cap, parse_mode="Markdown"))
            try:
                bot.send_media_group(chat_id, media_group)
            except Exception:
                bot.send_message(chat_id, card, parse_mode="Markdown")
                for p_id in photo_ids[:10]:
                    try:
                        bot.send_photo(chat_id, p_id, protect_content=True)
                    except Exception:
                        try:
                            bot.send_document(chat_id, p_id, protect_content=True)
                        except Exception:
                            pass
        else:
            bot.send_message(chat_id, card, parse_mode="Markdown")

        bot.answer_callback_query(call.id)

    # 6. إرسال محاولة حل لواجب (عرض الواجب أمامه مع خيار إرسال الحل مباشرة)
    @bot.message_handler(func=lambda message: message.text and message.text.startswith("📤 إرسال محاولة حل واجب"))
    def submit_solution_start(message):
        bot.send_message(
            message.chat.id,
            "📂 **اختر الوحدة لعرض الواجبات المفتوحة حالياً:**",
            reply_markup=submit_solution_units_menu(),
            parse_mode="Markdown"
        )

    @bot.message_handler(func=lambda message: message.text and message.text.startswith("وجبات "))
    def handle_submit_unit_selection(message):
        text = message.text
        hw_unit_id, _ = _find_unit_by_text(text.split("(")[0])
        if hw_unit_id:
            student_last_unit[message.chat.id] = hw_unit_id
        bot.send_message(
            message.chat.id,
            "📝 **اختر الواجب لعرض موضوعه وإرسال حلك:**",
            reply_markup=submit_solution_exercises_menu(text),
            parse_mode="Markdown"
        )

    @bot.message_handler(func=lambda message: message.text == "⬅️ رجوع إلى الوحدات")
    def back_to_submit_units(message):
        _clear_student_state(message.chat.id)
        bot.send_message(message.chat.id, "📂 **اختر الوحدة:**", reply_markup=submit_solution_units_menu(), parse_mode="Markdown")

    @bot.message_handler(func=lambda message: message.text and message.text.startswith("حل "))
    def handle_chosen_exercise(message):
        chat_id = message.chat.id
        ex_name = message.text[len("حل "):].strip()
        if not throttle(("hw", chat_id, ex_name), 3):
            return

        u_id = student_last_unit.get(chat_id)
        hw_row = None
        with db_lock:
            if u_id:
                cursor.execute(
                    "SELECT id, pdf_file_id, extra_media FROM exercises WHERE unit_id = ? AND name = ? AND is_active = 1 AND item_type = 'homework' ORDER BY id DESC",
                    (u_id, ex_name))
                hw_row = cursor.fetchone()
            if not hw_row:
                cursor.execute(
                    "SELECT id, pdf_file_id, extra_media FROM exercises WHERE name = ? AND is_active = 1 AND item_type = 'homework' ORDER BY id DESC",
                    (ex_name,))
                hw_row = cursor.fetchone()

        if not hw_row:
            bot.send_message(chat_id, "❌ هذا الواجب غير متاح لإرسال الحلول حالياً أو انتهت مهلته.")
            return

        hw_id, pdf_id, extra_json = hw_row
        student_temp_data[chat_id] = ex_name
        student_photos_cache[chat_id] = []

        # موضوع الواجب: كل الصور والملفات دفعة واحدة
        items = parse_media(extra_json, pdf_id)
        sent = send_media_bundle(bot, chat_id, f"📝 **موضوع الواجب المنزلي:** {_md(ex_name)}", items, protect=True)
        if not sent:
            bot.send_message(chat_id, "⚠️ تعذر إرسال ملفات الموضوع حالياً، أعد المحاولة بعد قليل.")

        bot.send_message(
            chat_id,
            "📸 **موضوع الواجب أمامك أعلاه.**\n\n"
            "الآن بعد إنجاز الحل في كراستك، قم بتصوير صفحات الحل وأرسلها هنا (يمكنك إرسال عدة صور أو ملف PDF).\n"
            "⚠️ **ملاحظة:** يُمنع إرسال مقاطع الفيديو.\n\n"
            "عند الانتهاء تماماً، اضغط على زر **(✅ تأكيد الإرسال)** أسفل الشاشة:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("✅ تأكيد الإرسال"), KeyboardButton("⬅️ رجوع"))
        )

    # حظر الفيديو
    @bot.message_handler(content_types=['video', 'video_note'])
    def block_video_submissions(message):
        bot.send_message(
            message.chat.id,
            "❌ **عذراً، لا يمكن إرسال الفيديو لحل الواجب.**\n"
            "يرجى تصوير صفحات الحل كصور واضحة أو إرسال ملف PDF."
        )

    # استلام صور أو ملفات الحل
    @bot.message_handler(content_types=['photo', 'document'])
    def handle_student_solution_media(message):
        chat_id = message.chat.id
        if chat_id not in student_temp_data:
            return

        file_id = message.photo[-1].file_id if message.photo else message.document.file_id
        if chat_id not in student_photos_cache:
            student_photos_cache[chat_id] = []

        if file_id not in student_photos_cache[chat_id]:
            student_photos_cache[chat_id].append(file_id)

        # عند إرسال عدة صور دفعة واحدة (ألبوم) نرد مرة واحدة فقط
        if not first_in_album(message):
            return
        if message.media_group_id:
            note = "📥 تم استلام صفحاتك ✅.\nأرسل المزيد أو اضغط على **✅ تأكيد الإرسال** لإنهاء الإرسال."
        else:
            note = f"📥 تم استلام الصفحة/المرفق رقم ({len(student_photos_cache[chat_id])}).\nأرسل صفحة أخرى أو اضغط على **✅ تأكيد الإرسال** لإنهاء الإرسال."
        bot.send_message(
            chat_id, note,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("✅ تأكيد الإرسال"), KeyboardButton("⬅️ رجوع"))
        )

    @bot.message_handler(func=lambda message: message.text == "✅ تأكيد الإرسال")
    def confirm_solution_sending(message):
        chat_id = message.chat.id
        if chat_id == ADMIN_ID and ADMIN_ID in db.admin_preview:
            _clear_student_state(chat_id)
            bot.send_message(chat_id, "👁️ وضع المعاينة: إرسال الحلول معطّل.", reply_markup=main_menu(chat_id))
            return
        if chat_id not in student_photos_cache or not student_photos_cache[chat_id]:
            bot.send_message(chat_id, "⚠️ لم تقم بإرسال أي صور أو ملفات للحل بعد!")
            return

        photo_ids = student_photos_cache[chat_id]
        ex_name = student_temp_data.get(chat_id, "الواجب")
        row = get_user(chat_id)
        student_name = row[1] if row else message.from_user.first_name

        with db_lock:
            # حذف أي سجل سابق لعلامة 00/20 التلقائية لهذا التلميذ على هذا الواجب
            cursor.execute("DELETE FROM submissions WHERE student_id = ? AND exercise_name = ? AND status = 'auto_zero'", (chat_id, ex_name))

            for p_id in photo_ids:
                cursor.execute(
                    "INSERT INTO submissions (student_id, student_name, exercise_name, photo_id, status) VALUES (?, ?, ?, ?, ?)",
                    (chat_id, student_name, ex_name, p_id, 'قيد الانتظار')
                )
            conn.commit()

        _clear_student_state(chat_id)

        bot.send_message(
            chat_id,
            f"🎉 **صحيت وصل الحل تاعك ({_md(ex_name)}) للأستاذ أسامة بنجاح ✅**\n\n"
            "سوف يتم مراجعته ورصد علامتك وملاحظات التوجيه قريباً..\n"
            "واصل هكذا وبلا عجز، راك قدها وربي يفرحنا بنجاحك في الباك 🌟",
            parse_mode="Markdown",
            reply_markup=main_menu(chat_id)
        )

    # 7. الفصل التام بين "الدروس" و "التمارين"
    @bot.message_handler(func=lambda message: message.text == "📘 الدروس")
    def lessons_menu_start(message):
        bot.send_message(
            message.chat.id,
            "📘 **محاور الدروس المشروحة:**\n"
            "خيّر المحور باش تشوف الدروس المتاحة 👇",
            reply_markup=units_menu("lesson"),
            parse_mode="Markdown"
        )

    @bot.message_handler(func=lambda message: message.text == "📝 التمارين والحلول")
    def exercises_menu_start(message):
        bot.send_message(
            message.chat.id,
            "📝 **سلاسل التمارين التطبيقية والحلول:**\n"
            "خيّر المحور لعرض التمارين مرتبة تصاعدياً لحلها خطوة بخطوة ✍️🔥",
            reply_markup=units_menu("exercise"),
            parse_mode="Markdown"
        )

    def _find_unit_by_text(unit_text):
        """مطابقة ذكية للوحدة مع الاسم الكامل أو بدون إيموجي."""
        clean_u = unit_text.replace("دروس", "").replace("تمارين", "").replace("وجبات", "").strip()
        with db_lock:
            cursor.execute("SELECT id, name FROM units WHERE is_active = 1")
            units = cursor.fetchall()
        for uid, uname in units:
            # جرّب المطابقة المباشرة أولاً (الاسم كاملاً بإيموجيه)
            if clean_u == uname.strip():
                return uid, uname
            # ثم مطابقة بدون إيموجي
            import re as _re
            clean_db = _re.sub(r'[\U00010000-\U0010ffff]', '', uname).strip()
            clean_u2 = _re.sub(r'[\U00010000-\U0010ffff]', '', clean_u).strip()
            if clean_u2 and (clean_u2 in clean_db or clean_db in clean_u2):
                return uid, uname
        return None, None

    # استعراض الدروس فقط
    @bot.message_handler(func=lambda message: message.text and message.text.startswith("دروس "))
    def handle_lessons_view(message):
        u_id, real_name = _find_unit_by_text(message.text)

        if not u_id:
            bot.send_message(message.chat.id, "❌ عذراً، هذا المحور غير متاح حالياً أو مغلق.")
            return
        student_last_unit[message.chat.id] = u_id

        with db_lock:
            cursor.execute("SELECT id, name FROM exercises WHERE unit_id = ? AND is_active = 1 AND item_type = 'lesson' ORDER BY id ASC", (u_id,))
            lessons = cursor.fetchall()

        if not lessons:
            bot.send_message(message.chat.id, f"📘 **{_md(real_name)}**:\nلا توجد دروس منشورة في هذا المحور حالياً.", reply_markup=main_menu(message.chat.id), parse_mode="Markdown")
            return

        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        for lid, lname in lessons:
            markup.add(KeyboardButton(f"📘 {lname}"))
        markup.add("⬅️ رجوع")

        bot.send_message(
            message.chat.id,
            f"📘 **دروس {_md(real_name)}:**\nاختر الدرس للتحميل:",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    # استعراض التمارين فقط (مرتبة تصاعدياً)
    @bot.message_handler(func=lambda message: message.text and message.text.startswith("تمارين "))
    def handle_exercises_view(message):
        u_id, real_name = _find_unit_by_text(message.text)

        if not u_id:
            bot.send_message(message.chat.id, "❌ عذراً، هذا المحور غير متاح حالياً أو مغلق.")
            return
        student_last_unit[message.chat.id] = u_id

        with db_lock:
            cursor.execute("SELECT id, name FROM exercises WHERE unit_id = ? AND is_active = 1 AND item_type = 'exercise' ORDER BY id ASC", (u_id,))
            exs = cursor.fetchall()

        if not exs:
            bot.send_message(message.chat.id, f"📝 **{_md(real_name)}**:\nلا توجد تمارين منشورة في هذا المحور حالياً.", reply_markup=main_menu(message.chat.id), parse_mode="Markdown")
            return

        # ترتيب التمارين تصاعدياً طبيعياً (1, 2, 3...)
        import re
        def nat_key(s):
            return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', str(s))]
        exs.sort(key=lambda x: (nat_key(x[1]), x[0]))

        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        for eid, ename in exs:
            markup.add(KeyboardButton(f"📝 {ename}"))
        markup.add("⬅️ رجوع")

        bot.send_message(
            message.chat.id,
            f"📝 **تمارين {_md(real_name)} (مرتبة تصاعدياً):**\nاختر التمرين للتحميل:",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    @bot.message_handler(func=lambda message: message.text and (message.text.startswith("📘") or message.text.startswith("📝")))
    def send_lesson_or_exercise_pdf(message):
        chat_id = message.chat.id
        text = message.text
        if text in ("📘 الدروس", "📝 التمارين والحلول"):
            return

        item_type = "lesson" if text.startswith("📘") else "exercise"

        # أسماء مرشحة: الاسم كما هو على الزر، ثم بدون بادئة "درس/تمرين" (للتوافق مع الأسماء القديمة)
        base = text.split(" ", 1)[1].strip() if " " in text else text[1:].strip()
        candidates = [base]
        for pfx in ("درس: ", "تمرين: ", "درس ", "تمرين "):
            if base.startswith(pfx) and base[len(pfx):].strip():
                candidates.append(base[len(pfx):].strip())
                break

        # منع تكرار الإرسال عند النقر المزدوج
        if not throttle(("content", chat_id, text), 3):
            return

        u_id = student_last_unit.get(chat_id)
        row = None
        with db_lock:
            for title in candidates:
                if u_id:
                    cursor.execute(
                        "SELECT pdf_file_id, extra_media, name FROM exercises WHERE unit_id = ? AND item_type = ? AND name = ? AND is_active = 1 ORDER BY id DESC",
                        (u_id, item_type, title))
                    row = cursor.fetchone()
                    if row:
                        break
                cursor.execute(
                    "SELECT pdf_file_id, extra_media, name FROM exercises WHERE item_type = ? AND name = ? AND is_active = 1 ORDER BY id DESC",
                    (item_type, title))
                row = cursor.fetchone()
                if row:
                    break

        if not row:
            bot.send_message(chat_id, "❌ عذراً، ملف هذا المحتوى غير متوفر حالياً.")
            return

        pdf_id, extra_json, real_name = row
        items = parse_media(extra_json, pdf_id)
        if not items:
            bot.send_message(chat_id, "❌ عذراً، لا توجد ملفات مرفقة بهذا المحتوى بعد.")
            return

        # كل الصور في ألبوم واحد وكل الملفات في ألبوم واحد
        sent = send_media_bundle(bot, chat_id, f"📄 **{_md(real_name)}**", items, protect=True)
        if not sent:
            bot.send_message(chat_id, "❌ حدث خطأ أثناء إرسال الملفات، أعد المحاولة بعد قليل.")

    # 8. زر الرجوع والمساعدة
    @bot.message_handler(func=lambda message: message.text == "⬅️ رجوع")
    def back_to_main(message):
        _clear_student_state(message.chat.id)
        bot.send_message(
            message.chat.id, 
            "القائمة الرئيسية:",
            reply_markup=main_menu(message.chat.id),
            parse_mode="Markdown"
        )

    @bot.message_handler(func=lambda message: message.text == "ℹ️ مساعدة")
    def help_info(message):
        bot.send_message(
            message.chat.id,
            "💡 **دليل استخدام بوت الأستاذ بلعقاب أسامة:**\n\n"
            "• **📘 الدروس:** استعراض وتحميل دروس كل محور على حدة كما أعدها الأستاذ.\n"
            "• **📝 التمارين والحلول:** سلاسل التمارين مرتبة تصاعدياً بالمحاور للممارسة والتدريب.\n"
            "• **📤 إرسال حل واجب:** عرض موضوع الواجب وتصوير كراستك وإرسال صفحات الحل للأستاذ.\n"
            "• **📊 علامتي في الواجبات:** بطاقة تقييمك الرسمية مع التاريخ، علامتك، وملاحظات الأستاذ.\n"
            "• **📂 مواضيع BAC:** حوليات البكالوريا الرسمية مصنفة بالشعب ومرتبة من العام الكبير إلى الصغير.\n"
            "• **📁 ملفات متنوعة:** ملخصات وقوانين وسلاسل إضافية للتحميل.\n"
            "• **💬 تواصل مع الأستاذ:** محادثة مباشرة (ستايل إنستغرام) مع الأستاذ أسامة (تُحذف بعد 24 ساعة).\n"
            "• **🤖 البوت الآلي:** مساعد حل التمارين الذكي (قيد التطوير بالذكاء الاصطناعي).",
            parse_mode="Markdown"
        )