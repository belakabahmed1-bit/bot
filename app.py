"""نقطة الانطلاق والتشغيل الرئيسية لبوت الرياضيات للأستاذ بلعقاب أسامة."""
import os
import time
import traceback

# توقيت الجزائر (خوادم Railway تعمل بتوقيت UTC). يجب ضبطه قبل أي استيراد آخر.
os.environ["TZ"] = os.environ.get("BOT_TZ", "Africa/Algiers")
if hasattr(time, "tzset"):
    time.tzset()

import re
import secrets
import string
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)
import db
from db import (
    conn, cursor, user_steps, temp_data, attempts,
    get_user, get_wait, activate_from_wait, track_activity
)
from user_panel import main_menu, register_user_handlers
from admin_panel import (
    admin_reply_menu, handle_admin_callback, setup_admin_panel_handlers,
    send_admin_panel, reset_admin_state
)
from utils import install_update_dedup, install_safe_api
from filestore import start_file_sync


TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود في متغيرات البيئة")
ADMIN_ID = 5279737033

INSTAGRAM_URL = "https://www.instagram.com/prof_oussama_maths"

SCHOOLS = [
    "مدرسة القمة رغاية",
    "مدرسة الأمين رويبة",
    "مدرسة القمة باب الزوار",
    "مدرسة ماسي الحلايمية",
    "دراسة عن بعد 🌐"
]



class ErrorHandler(telebot.ExceptionHandler):
    """خطأ داخل معالج واحد لا يجب أن يوقف البوت أو يعيد تشغيل الاستطلاع."""

    def handle(self, exception):
        print("Handler error:", repr(exception))
        traceback.print_exception(type(exception), exception, exception.__traceback__)
        return True


bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=8, exception_handler=ErrorHandler())
install_update_dedup(bot)   # تجاهل التحديث الواحد إذا وصل مرتين
install_safe_api(bot)       # حماية الرسائل من أخطاء Markdown


def is_admin(uid):
    return uid == ADMIN_ID


def generate_code():
    a = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(a) for _ in range(6))


def is_valid_arabic_name(name):
    """التحقق الصارم: حروف عربية فقط ومسافات (ممنوع الحروف اللاتينية والأرقام والرموز والإيموجي)."""
    name = name.strip()
    # يسمح بالحروف العربية من الهمزة إلى الياء والمسافات فقط
    if not re.fullmatch(r"^[\u0621-\u064A\s]+$", name):
        return False
    # التحقق من وجود كلمتين على الأقل (الاسم واللقب)
    words = [w for w in name.split() if len(w) >= 2]
    return len(words) >= 2


def schools_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for s in SCHOOLS:
        markup.add(KeyboardButton(s))
    return markup


def instagram_markup():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📸 مراسلة الأستاذ على إنستغرام لطلب الكود", url=INSTAGRAM_URL))
    return markup


# تتبّع النشاط اليومي لكل تلميذ
def _track(m):
    try:
        track_activity(m.from_user.id)
    except Exception as e:
        print("track error:", e)
    return False


@bot.message_handler(func=_track, content_types=['text', 'photo', 'document', 'video', 'voice'])
def _never(m):
    pass


# أمر البدء /start
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id

    # الأستاذ (الأدمن)
    if is_admin(uid):
        db.admin_preview.discard(uid)
        reset_admin_state(message.chat.id)
        bot.send_message(message.chat.id, "أهلاً بك يا أستاذنا بلعقاب أسامة! يعطيك الصحة.. لوحة التحكم بين يديك 👑", reply_markup=admin_reply_menu())
        send_admin_panel(bot, message.chat.id, "👑 **لوحة تحكم الأدمين:**")
        return

    # تلميذ مفعّل مسبقاً
    if get_user(uid):
        user_steps.pop(uid, None)
        bot.send_message(
            message.chat.id,
            "واش راك! مرحباً بك مجدداً معنا 🚀",
            reply_markup=main_menu(uid),
            parse_mode="Markdown"
        )
        return

    # تلميذ قيد الانتظار للتفعيل
    if get_wait(uid):
        user_steps[uid] = "activate"
        bot.send_message(
            message.chat.id,
            "🔐 **حسابك راهو في قائمة الانتظار!**\n\n"
            "باش تتحصل على كود التفعيل، ادخل درك لصفحة الأستاذ أسامة في إنستغرام وابعتلو ميساج 📩\n\n"
            "كليكي على الزر التحتاني باش تروح لصفحته مباشرة، ومن بعد كي يعطيك الكود اكتبو هنا وبصحتك الدخول 🚀\n\n",
            reply_markup=instagram_markup(),
            parse_mode="Markdown"
        )
        return

    # تلميذ جديد يسجل لأول مرة
    bot.send_message(
        message.chat.id,
        "👋 **مرحباً بك! 🚀**\n\n"
        "📚 هذا البوت الرسمي لتلاميذ **الأستاذ بلعقاب أسامة** لمادة الرياضيات لتحضير شهادة البكالوريا بأقوى مستوى 🔥\n\n"
        "🔹 **واش تلقى هنا:**\n"
        "• دروس وتمارين مشروحة مع الحلول المفصلة 📈\n"
        "• حلول الواجبات وتصحيحها بكروت علامات وملاحظات الأستاذ ✍️\n"
        "• حوليات وأرشيف البكالوريا الرسمية BAC مرتبة 📂\n"
        "• ملفات ومخلصات وسلاسل مراجعة قوية 📁\n"
        "• محادثة مباشرة مع الأستاذ أسامة لطرح استفساراتك 💬\n\n"
        "📐 *«المات مع أسامة في سلامة.. تقدرولهااااا خاوتي!»* 🚀💪\n\n"
        "🔐 **للمتابعة، أكمل التسجيل أولاً 👇**",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    bot.send_message(
        message.chat.id,
        "👤 **اكتب اسمك ولقبك الحقيقي بالعربية:**\n✏️ (مثال: محمد بلقاسم)",
        parse_mode="Markdown"
    )
    user_steps[uid] = "full_name"


# معالجة خطوات التسجيل والتفعيل
@bot.message_handler(func=lambda m: m.from_user.id in user_steps)
def process_registration(message):
    uid = message.from_user.id
    step = user_steps.get(uid)
    text = (message.text or "").strip()
    if not text:
        return

    # 1. خطوة الاسم واللقب (إجبار الحروف العربية فقط)
    if step == "full_name":
        if not is_valid_arabic_name(text):
            bot.send_message(
                message.chat.id,
                "⚠️ **تنبيه:** اكتب اسمك ولقبك الحقيقي بالحروف العربية فقط (الاسم واللقب مع بعض).\n"
                "❌ ممنوع الحروف اللاتينية (Français/English)، الأرقام، الرموز ولا الإيموجي.\n\n"
                "✏️ **عاود اكتب اسمك ولقبك بالعربية (مثال: محمد بلقاسم):**",
                parse_mode="Markdown"
            )
            return

        temp_data[uid] = {"full_name": text}
        user_steps[uid] = "phone"
        bot.send_message(
            message.chat.id,
            "📱 **الخطوة 2 من 3: أدخل رقم هاتفك المحمول:**\n"
            "✏️ (مثال: `0555123456` أو `0666123456` أو `0777123456`)\n\n"
            "💡 *هذا الرقم ستستخدمه للدخول إلى موقع الأستاذ بلعقاب أسامة مع الرمز السري الخاص بك.*",
            parse_mode="Markdown"
        )

    # 2. خطوة رقم الهاتف
    elif step == "phone":
        import re
        clean_phone = re.sub(r'[\s\-\(\)]', '', text)
        if clean_phone.startswith("+213"):
            clean_phone = "0" + clean_phone[4:]
        elif clean_phone.startswith("00213"):
            clean_phone = "0" + clean_phone[5:]

        if not re.match(r"^0[567][0-9]{8}$", clean_phone):
            bot.send_message(
                message.chat.id,
                "⚠️ **رقم الهاتف غير صحيح!**\n\n"
                "يرجى إدخال رقم هاتف جزائري صالح يتكون من 10 أرقام ويبدأ بـ (05 أو 06 أو 07):\n"
                "✏️ (مثال: `0555123456`):",
                parse_mode="Markdown"
            )
            return

        if uid not in temp_data:
            temp_data[uid] = {}
        temp_data[uid]["phone"] = clean_phone
        user_steps[uid] = "school"
        bot.send_message(
            message.chat.id,
            "🏫 **الخطوة 3 من 3: اختر المدرسة التي تدرس فيها من الأزرار أدناه:**",
            reply_markup=schools_keyboard(),
            parse_mode="Markdown"
        )

    # 3. خطوة اختيار المدرسة
    elif step == "school":
        if text not in SCHOOLS:
            bot.send_message(
                message.chat.id,
                "⚠️ الرجاء اختيار مدرستك بالضغط على أحد الأزرار:",
                reply_markup=schools_keyboard()
            )
            return

        d = temp_data.pop(uid, None)
        if not d:
            user_steps.pop(uid, None)
            bot.send_message(uid, "اكتب /start للتسجيل من جديد.", reply_markup=ReplyKeyboardRemove())
            return

        full_name = d["full_name"]
        phone = d.get("phone", "")
        code = generate_code()
        from datetime import datetime
        now_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        cursor.execute(
            "INSERT OR REPLACE INTO wait (user_id, full_name, school, code, created_at, phone) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, full_name, text, code, now_time, phone)
        )
        conn.commit()

        user_steps[uid] = "activate"

        bot.send_message(
            message.chat.id,
            f"✅ **تم تسجيل بياناتك بنجاح!**\n\n"
            f"👤 **الاسم واللقب:** {full_name}\n"
            f"📱 **رقم الهاتف:** `{phone}`\n"
            f"🏫 **المدرسة:** {text}\n\n"
            "🔐 **درك بقاتلك خطوة تفعيل الكود:**\n"
            "باش تتحصل على كود التفعيل والرمز السري، ابعث رسالة للأستاذ أسامة في حسابه إنستغرام 📩\n\n"
            "كليكي على الزر التحتاني باش تروح لصفحته وكي يعطيك الكود اكتبو هنا في البوت:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        bot.send_message(
            message.chat.id,
            "👇 كليكي هنا لمراسلة الأستاذ أسامة في إنستغرام:",
            reply_markup=instagram_markup()
        )

    # 4. خطوة إدخال كود التفعيل
    elif step == "activate":
        data = get_wait(uid)  # (user_id, full_name, school, code, created_at, phone)
        if not data:
            user_steps.pop(uid, None)
            if get_user(uid):
                bot.send_message(uid, "✅ حسابك مفعّل بالفعل! مرحباً بك.", reply_markup=main_menu(uid))
            else:
                bot.send_message(uid, "اكتب /start للتسجيل من جديد.")
            return

        if attempts.get(uid, 0) >= 5:
            bot.send_message(uid, "🚫 تجاوزت 5 محاولات خاطئة. تواصل مع الأستاذ أسامة مباشرة.")
            return

        if text.strip().upper() == data[3].strip().upper():
            res = activate_from_wait(uid)
            phone_val = (res.get("phone") if isinstance(res, dict) else "") or (data[5] if len(data) > 5 else "")
            code_val = (res.get("code") if isinstance(res, dict) else "") or data[3]

            bot.send_message(
                uid,
                f"🎉 **بصحتك! تفعل حسابك بنجاح في البوت والموقع 🚀**\n\n"
                f"مرحبا بيك في عائلة الرياضيات مع **الأستاذ بلعقاب أسامة**، ربي يوفقك ويفرحنا بنجاحك في الباك بالامتياز 🎓✨\n\n"
                f"🌐 **بيانات تسجيل دخولك لموقع الأستاذ الإلكتروني:**\n"
                f"📱 **رقم الهاتف:** `{phone_val}`\n"
                f"🔑 **الرمز السري:** `{code_val}`\n\n"
                f"⚠️ **تنبيه:** هذا الرمز السري دائم وخاص بك ولا يمكن تغييره. استعمله دائماً لتسجيل الدخول إلى الموقع وتصفح كافة الدروس والتمارين والفيديوهات 📐\n\n"
                f"📐 *«المات مع أسامة في سلامة.. تقدرولهااااا خاوتي!»* 💪",
                reply_markup=main_menu(uid),
                parse_mode="Markdown"
            )
        else:
            attempts[uid] = attempts.get(uid, 0) + 1
            left = 5 - attempts[uid]
            bot.send_message(
                uid,
                f"❌ الكود غير صحيح. تبقت لديك ({left}) محاولات.\nتأكد مليح من الكود ولا اطلبو من الأستاذ أسامة في إنستغرام."
            )


# إعداد معالجات لوحة الأستاذ ولوحة التلميذ
setup_admin_panel_handlers(bot)
register_user_handlers(bot)


# توجيه الـ Callbacks الخاصة بالأدمن فقط، حتى لا يتم حظر Callbacks التلاميذ
@bot.callback_query_handler(func=lambda call: is_admin(call.from_user.id))
def admin_callbacks(call):
    handle_admin_callback(bot, call)


if __name__ == "__main__":
    print("Bot starting...")
    start_file_sync(bot)   # نسخ الملفات والصور إلى Turso للموقع

    # تشغيل خادم الموقع الإلكتروني المدمج في الخلفية (مرتبط بـ Turso)
    try:
        import threading
        from web_server import run as run_web_server
        web_thread = threading.Thread(target=run_web_server, daemon=True)
        web_thread.start()
        print(f"Web server started on port {os.environ.get('PORT', 5000)}")
    except Exception as e:
        print("Web server startup error:", e)
    try:
        bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query"],
    )
