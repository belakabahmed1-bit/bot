"""نقطة الانطلاق والتشغيل الرئيسية لبوت الرياضيات للأستاذ بلعقاب أسامة."""
import os
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
from admin_panel import admin_main_menu, admin_reply_menu, handle_admin_callback, setup_admin_panel_handlers


TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود في متغيرات البيئة")
ADMIN_ID = 5279737033

INSTAGRAM_URL = "https://www.instagram.com/prof_oussama_maths"

SCHOOLS = [
    "مدرسة القمة رغاية",
    "مدرسة الأمين رويبة",
    "مدرسة القمة باب الزوار",
    "مدرسة ماسي الحلايمية"
]

bot = telebot.TeleBot(TOKEN)


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
        bot.send_message(message.chat.id, "أهلاً بك يا أستاذنا بلعقاب أسامة! يعطيك الصحة.. لوحة التحكم بين يديك 👑", reply_markup=admin_reply_menu())
        bot.send_message(message.chat.id, "👑 **لوحة تحكم الأدمين:**", reply_markup=admin_main_menu(), parse_mode="Markdown")
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

    # خطوة الاسم واللقب (إجبار الحروف العربية فقط)
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
        user_steps[uid] = "school"
        bot.send_message(
            message.chat.id,
            "🏫 **اختر المدرسة التي تدرس فيها من الأزرار أدناه:**",
            reply_markup=schools_keyboard(),
            parse_mode="Markdown"
        )

    # خطوة اختيار المدرسة
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
        code = generate_code()
        from datetime import datetime
        now_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        cursor.execute(
            "INSERT OR REPLACE INTO wait (user_id, full_name, school, code, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, full_name, text, code, now_time)
        )
        conn.commit()

        user_steps[uid] = "activate"

        bot.send_message(
            message.chat.id,
            f"✅ **تم تسجيل بياناتك بنجاح!**\n\n"
            f"👤 **الاسم واللقب:** {full_name}\n"
            f"🏫 **المدرسة:** {text}\n\n"
            "🔐 **درك بقاتلك خطوة تفعيل الكود:**\n"
            "باش تتحصل على كود التفعيل، ابعث رسالة للأستاذ أسامة في حسابه إنستغرام 📩\n\n"
            "كليكي على الزر التحتاني باش تروح لصفحته وكي يعطيك الكود اكتبو هنا في البوت:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        bot.send_message(
            message.chat.id,
            "👇 كليكي هنا لمراسلة الأستاذ أسامة في إنستغرام:",
            reply_markup=instagram_markup()
        )

    # خطوة إدخال كود التفعيل
    elif step == "activate":
        data = get_wait(uid)  # (user_id, full_name, school, code, created_at)
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
            activate_from_wait(uid)
            bot.send_message(
                uid,
                "🎉 **بصحتك! تفعل حسابك بنجاح 🚀**\n\n"
                "مرحبا بيك في عائلة الرياضيات مع **الأستاذ بلعقاب أسامة**، ربي يوفقك ويفرحنا بنجاحك في الباك بالامتياز 🎓✨\n\n"
                "📐 *«المات مع أسامة في سلامة.. تقدرولهااااا خاوتي!»* 💪",
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
    try:
        bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    bot.infinity_polling()
