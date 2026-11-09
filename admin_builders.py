"""دوال مساعدة وبناء واجهات القوائم للوحة تحكم المشرف (Admin Panel)."""
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from db import cursor, get_setting

MAX_ROWS = 40


def esc(s):
    """هروب رموز Markdown حتى لا تنكسر الرسالة بسبب رموز خاصة."""
    s = "" if s is None else str(s)
    for ch in ("\\", "_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


def _back(markup, target="admin_home"):
    markup.add(InlineKeyboardButton("🔙 رجوع", callback_data=target))
    return markup


# ------------------- 1. قائمة الانتظار -------------------
def build_wait_list(rows, prefix="del_wait"):
    markup = InlineKeyboardMarkup()
    text = f"🤖 **قائمة انتظار بوت التلغرام ({len(rows)} طلب معلّق):**\n\n"
    if not rows:
        return "🚫 لا توجد طلبات انتظار حالياً من بوت التلغرام.", _back(markup)
    for r in rows[:MAX_ROWS]:
        user_id = r[0]
        name = r[1]
        school = r[2]
        c = r[3]
        created_at = r[4]
        phone = r[5] if len(r) > 5 and r[5] else "غير مسجل"
        text += (
            f"👤 *الاسم واللقب:* {esc(name)}\n"
            f"📱 *رقم الهاتف:* `{phone}`\n"
            f"🔑 *الرمز السري:* `{c}`\n"
            f"🏷️ *المصدر:* 🤖 بوت التلغرام 📲\n"
            f"🏫 *المدرسة:* {esc(school)}\n"
            f"🆔 *الآيدي:* `{user_id}`\n"
            f"🗓️ *تاريخ الطلب:* {created_at or '—'}\n"
            f"──────────────────\n"
        )
        markup.add(
            InlineKeyboardButton(f"✅ قبول {name}", callback_data=f"ok_wait_{user_id}"),
            InlineKeyboardButton("❌ رفض وطرد", callback_data=f"{prefix}_{user_id}")
        )
    markup.add(InlineKeyboardButton("🌐 الانتقال لقائمة انتظار الموقع", callback_data="web_wait_menu"))
    return text, _back(markup)


def build_web_wait_list(rows):
    markup = InlineKeyboardMarkup()
    text = f"🌐 **قائمة انتظار الموقع الإلكتروني ({len(rows)} طلب معلّق):**\n\n"
    if not rows:
        return "🚫 لا توجد طلبات تسجيل معلقة من الموقع الإلكتروني حالياً.", _back(markup)
    for r in rows[:MAX_ROWS]:
        wid = r[0]
        name = f"{r[1]} {r[2]}".strip()
        phone = r[3]
        school = r[4]
        branch = r[5]
        code = r[6]
        created_at = r[7] if len(r) > 7 else "—"
        text += (
            f"👤 *الاسم واللقب:* {esc(name)}\n"
            f"📱 *رقم الهاتف:* `{phone}`\n"
            f"🔑 *الرمز السري:* `{code}`\n"
            f"🏷️ *المصدر:* 🌐 الموقع الإلكتروني 💻\n"
            f"🏫 *المدرسة:* {esc(school)}\n"
            f"📚 *الشعبة:* {esc(branch)}\n"
            f"🗓️ *تاريخ التسجيل:* {created_at or '—'}\n"
            f"──────────────────\n"
        )
        markup.add(
            InlineKeyboardButton(f"✅ تفعيل وقبول {name}", callback_data=f"ok_web_wait_{wid}"),
            InlineKeyboardButton("❌ رفض وحظر", callback_data=f"del_web_wait_{wid}")
        )
    return text, _back(markup)


# ------------------- 2. قوائم المفعلين مفصولة (تلاميذ البوت وحدها وتلاميذ الموقع وحدها) -------------------
def build_bot_users_list(rows):
    """عرض تلاميذ بوت التلغرام المفعلين فقط (الهاتف) مع إمكانية مراسلتهم بالـ ID مباشرة."""
    markup = InlineKeyboardMarkup()
    text = f"🤖 **قائمة تلاميذ بوت التلغرام المفعلين ({len(rows)} تلميذ):**\n"
    text += "📱 *(مسجلون عبر الهاتف - يمكنك مراسلتهم بالـ ID وتفقد نشاطهم)*\n\n"
    if not rows:
        return "🚫 لا يوجد تلاميذ مفعلين في بوت التلغرام حالياً.", _back(markup)
    for r in rows[:MAX_ROWS]:
        user_id = r[0]
        name = r[1]
        school = r[2]
        last_active = r[3]
        phone = r[5] if len(r) > 5 and r[5] else "غير مسجل"
        code = r[6] if len(r) > 6 and r[6] else "—"

        text += f"👤 *{esc(name)}* | 🆔 `{user_id}`\n"
        text += f"📱 الهاتف: `{phone}` | 🔑 الرمز السري: `{code}`\n"
        text += f"🏫 {esc(school)}\n"
        text += f"🕒 آخر ظهور: {last_active or '—'}\n"
        text += f"──────────────────\n"
        markup.add(
            InlineKeyboardButton("📅 النشاط", callback_data=f"UD_{user_id}"),
            InlineKeyboardButton("✉️ مراسلة بالـ ID", callback_data=f"msg_user_{user_id}"),
            InlineKeyboardButton("❌ حذف من البوت", callback_data=f"del_user_{user_id}")
        )
    if len(rows) > MAX_ROWS:
        text += f"… (يُعرض أول {MAX_ROWS} من {len(rows)})\n"
    markup.add(InlineKeyboardButton("💻 الانتقال لتلاميذ الموقع الإلكتروني 🌐", callback_data="web_users_menu"))
    return text, _back(markup)


def build_web_users_list(rows):
    """عرض تلاميذ الموقع الإلكتروني المفعلين فقط (الحاسوب/الويب) مستقلين تماماً."""
    markup = InlineKeyboardMarkup()
    text = f"💻 **قائمة تلاميذ الموقع الإلكتروني المفعلين ({len(rows)} تلميذ):**\n"
    text += "🌐 *(مسجلون عبر الموقع والحاسوب)*\n\n"
    if not rows:
        return "🚫 لا يوجد تلاميذ مفعلين في الموقع الإلكتروني حالياً.", _back(markup)
    for r in rows[:MAX_ROWS]:
        wid = r[0]
        name = f"{r[1]} {r[2]}".strip()
        school = r[3] or "دراسة عن بعد 🌐"
        branch = r[4] or ""
        phone = r[5] or "غير مسجل"
        code = r[6] or "—"
        created_at = r[7] or "—"

        sch_text = f"{school} ({branch})" if branch else school
        text += f"👤 *{esc(name)}* | 💻 رقم العضوية: `W-{wid}`\n"
        text += f"📱 الهاتف: `{phone}` | 🔑 الرمز السري: `{code}`\n"
        text += f"🏫 {esc(sch_text)}\n"
        text += f"🗓️ تاريخ التسجيل: {created_at}\n"
        text += f"──────────────────\n"
        markup.add(
            InlineKeyboardButton("💬 مراسلة التلميذ", callback_data=f"reply_chat_{wid}"),
            InlineKeyboardButton("🚫 حظر من الموقع", callback_data=f"del_web_wait_{wid}")
        )
    if len(rows) > MAX_ROWS:
        text += f"… (يُعرض أول {MAX_ROWS} من {len(rows)})\n"
    markup.add(InlineKeyboardButton("📱 الانتقال لتلاميذ بوت التلغرام 🤖", callback_data="bot_users"))
    return text, _back(markup)


def build_users_list(rows):
    return build_bot_users_list(rows)


def build_student_info_card(student):
    src_icon = "🌐 الموقع الإلكتروني" if student.get("source_type") == "web" else "🤖 بوت التلغرام"
    text = (
        f"📋 **بطاقة التلميذ والرمز السري:**\n\n"
        f"👤 *الاسم واللقب:* {esc(student.get('name', ''))}\n"
        f"📱 *رقم الهاتف:* `{student.get('phone', '—')}`\n"
        f"🔑 *الرمز السري:* `{student.get('code', '—')}`\n"
        f"🏷️ *المصدر:* {src_icon}\n"
        f"🏫 *المدرسة:* {esc(student.get('school', '—'))}\n"
        f"📚 *الشعبة:* {esc(student.get('branch', '—'))}\n"
        f"📊 *حالة الحساب:* {student.get('status', '—')}\n"
        f"🗓️ *تاريخ التسجيل:* {student.get('created_at', '—')}\n"
    )
    markup = InlineKeyboardMarkup()
    st_id = student.get("id")
    active = student.get("active", 0)
    phone = student.get("phone", "")

    if student.get("source_type") == "web":
        if active == 0:
            markup.add(InlineKeyboardButton("✅ قبول وتفعيل الحساب", callback_data=f"ok_web_wait_{st_id}"))
        elif active == 1:
            markup.add(InlineKeyboardButton("❌ حظر / طرد التلميذ", callback_data=f"del_web_wait_{st_id}"))
        elif active == -1:
            markup.add(InlineKeyboardButton("♻️ رفع الحظر وتفعيل", callback_data=f"ok_web_wait_{st_id}"))
    else:
        if active == 0:
            markup.add(InlineKeyboardButton("✅ قبول وتفعيل في البوت", callback_data=f"ok_wait_{st_id}"))
        elif active == 1:
            markup.add(InlineKeyboardButton("❌ حذف التلميذ من البوت", callback_data=f"del_user_{st_id}"))

    markup.add(InlineKeyboardButton("🔍 بحث عن تلميذ آخر", callback_data="find_student_code"))
    markup.add(InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="admin_home"))
    return text, markup


# ------------------- 3. تصنيف المدارس -------------------
def build_schools_menu():
    cursor.execute("SELECT school, COUNT(*) FROM users GROUP BY school ORDER BY COUNT(*) DESC")
    markup = InlineKeyboardMarkup()
    for school, cnt in cursor.fetchall():
        markup.row(
            InlineKeyboardButton(f"🏫 {school} ({cnt} تلميذ)", callback_data=f"school_{(school or '')[:20]}"),
            InlineKeyboardButton("📢 مراسلة", callback_data=f"msg_school_{(school or '')[:20]}")
        )
    return _back(markup)


def build_school_users_list(school_name, rows):
    markup = InlineKeyboardMarkup()
    text = f"🏫 *{esc(school_name)}* ({len(rows)} تلميذ)\n\n"
    for user_id, name, school, last_active, joined_at in rows[:MAX_ROWS]:
        text += f"🆔 `{user_id}` | 👤 {esc(name)} | 🕒 {last_active or '—'}\n"
        markup.add(
            InlineKeyboardButton(f"📅 النشاط", callback_data=f"UD_{user_id}"),
            InlineKeyboardButton(f"✉️ مراسلة", callback_data=f"msg_user_{user_id}")
        )
    markup.add(InlineKeyboardButton(f"📢 مراسلة جميع تلاميذ هذه المدرسة", callback_data=f"msg_school_{school_name[:20]}"))
    return text, _back(markup, "schools_menu")


# ------------------- 4. المحذوفون بالتفاصيل الكاملة -------------------
def build_deleted_list(rows):
    markup = InlineKeyboardMarkup()
    text = f"🗑️ **أرشيف الحسابات المحذوفة ({len(rows)}):**\n\n"
    if not rows:
        return "🚫 لا يوجد تلاميذ في قائمة المحذوفين حالياً.", _back(markup)

    for user_id, name, school, joined_at, last_active, deleted_at in rows[:MAX_ROWS]:
        text += (
            f"👤 *الاسم:* {esc(name)}\n"
            f"🆔 *الآيدي:* `{user_id}`\n"
            f"🏫 *المدرسة:* {esc(school or 'غير محدد')}\n"
            f"🗓️ *تاريخ أول استخدام (التسجيل):* {joined_at or 'غير محدد'}\n"
            f"🕒 *آخر استخدام:* {last_active or 'غير متوفر'}\n"
            f"🗑️ *تاريخ الحذف:* {deleted_at or 'غير متوفر'}\n"
            f"──────────────────\n"
        )
        markup.add(InlineKeyboardButton(f"♻️ استرجاع {name}", callback_data=f"res_del_{user_id}"))

    return text, _back(markup)


# ------------------- 5. ترتيب أفضل المستخدمين -------------------
def build_ranking_list(rows):
    markup = InlineKeyboardMarkup()
    text = "🏆 **ترتيب أفضل التلاميذ (حسب عدد أيام التفاعل مع البوت):**\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, (user_id, name, school, days) in enumerate(rows[:MAX_ROWS]):
        m = medals[i] if i < 3 else f"{i + 1}."
        text += f"{m} *{esc(name)}* ({esc(school)}) — 🔥 `{days}` يوم نشاط\n"
        markup.add(InlineKeyboardButton(f"{m} {name} — {days} يوم", callback_data=f"UD_{user_id}"))
    return text, _back(markup)


# ------------------- 6. الحذف السريع والاسترجاع السريع -------------------
def build_quick_delete_list(users):
    markup = InlineKeyboardMarkup()
    text = f"⚡ **الحذف السريع للتلاميذ ({len(users)} معروضين):**\nاضغط على أي زر لحذف التلميذ فوراً ونقله للمحذوفين:\n\n"
    for row in users[:15]:
        user_id, name, school = row[0], row[1], row[2]
        last_active = row[3] if len(row) > 3 else "—"
        text += f"• 🆔 `{user_id}` | 👤 {esc(name)} ({esc(school)}) | 🕒 {last_active or '—'}\n"
        markup.add(InlineKeyboardButton(f"❌ حذف {name}", callback_data=f"del_quick_{user_id}"))

    if len(users) > 1:
        markup.add(InlineKeyboardButton(f"🗑️ حذف جميع هؤلاء ({min(15, len(users))}) دفعة واحدة", callback_data="del_quick_all"))

    return text, _back(markup)


def build_quick_restore_list(rows):
    markup = InlineKeyboardMarkup()
    text = f"⚡ **الاسترجاع السريع للحسابات المحذوفة ({len(rows)} معروضين):**\nاضغط على أي زر لاسترجاع الحساب فوراً وإعادته للمفعلين:\n\n"
    for row in rows[:15]:
        user_id, name, school = row[0], row[1], row[2]
        deleted_at = row[5] if len(row) > 5 else "—"
        text += f"• 🆔 `{user_id}` | 👤 {esc(name)} (حُذف: {deleted_at})\n"
        markup.add(InlineKeyboardButton(f"♻️ استرجاع {name}", callback_data=f"res_quick_{user_id}"))

    if len(rows) > 1:
        markup.add(InlineKeyboardButton(f"♻️ استرجاع جميع هؤلاء ({min(15, len(rows))}) دفعة واحدة", callback_data="res_quick_all"))

    return text, _back(markup)


# ------------------- 7. تسيير الوحدات والدروس والتمارين (بدون إضافة وحدات) -------------------
def build_units_management(units):
    markup = InlineKeyboardMarkup()
    text = (
        "⚙️ **تسيير الوحدات والتمارين والدروس:**\n\n"
        "• اضغط على **حالة الوحدة** لفتحها أو غلقها بنقرة واحدة (🟢 مفتوحة ⇄ 🔴 مغلقة).\n"
        "• اضغط على **المحتوى** لإضافة دروس أو تمارين بداخلها وإدارتها.\n"
    )
    for u_id, u_name, is_active in units:
        status_btn = "🟢 مفتوحة" if is_active == 1 else "🔴 مغلقة"
        markup.add(
            InlineKeyboardButton(f"{u_name} [{status_btn}]", callback_data=f"toggle_unit_{u_id}"),
            InlineKeyboardButton("📂 المحتوى", callback_data=f"ctrl_unit_content_{u_id}")
        )
    return text, _back(markup)


def build_unit_content_management(unit_name, unit_id, items):
    markup = InlineKeyboardMarkup()
    text = (
        f"📁 **إدارة محتوى وحدة:** {esc(unit_name)}\n\n"
        "• اضغط على العنصر لتبديل حالته بنقرة واحدة:\n"
        "  (🟢 مفعل ⇄ 🔴 مغلق)\n"
    )
    for ex_id, ex_name, is_active, item_type in items:
        status_icon = "🟢" if is_active == 1 else "🔴"
        type_icon = "📘" if item_type == 'lesson' else "📝"
        markup.add(
            InlineKeyboardButton(f"{status_icon} {type_icon} {ex_name}", callback_data=f"toggle_ex_status_{ex_id}"),
            InlineKeyboardButton("❌ حذف", callback_data=f"del_ex_item_{ex_id}")
        )

    markup.add(
        InlineKeyboardButton("➕ إضافة درس جديد", callback_data=f"add_item_kind_lesson_{unit_id}"),
        InlineKeyboardButton("➕ إضافة تمرين جديد", callback_data=f"add_item_kind_exercise_{unit_id}")
    )
    markup.add(InlineKeyboardButton("🔙 رجوع للوحدات", callback_data="manage_units_control"))
    return text, markup


# ------------------- 8. إدارة الواجبات المنزلية -------------------
def build_homework_management(units_data):
    markup = InlineKeyboardMarkup()
    text = (
        "📚 **إدارة الواجبات المنزلية:**\n\n"
        "اختر الوحدة لعرض واجباتها وإدارتها:\n"
    )
    for u_id, u_name, count in units_data:
        import re as _re
        clean_name = _re.sub(r'[\U00010000-\U0010ffff]', '', u_name).strip()
        count_tag = f"({count} واجبات)" if count > 0 else "(0)"
        markup.row(
            InlineKeyboardButton(f"📂 {clean_name} {count_tag}", callback_data=f"admin_hw_unit_{u_id}"),
            InlineKeyboardButton("🗑️ حذف الوحدة", callback_data=f"del_hw_unit_{u_id}")
        )
    markup.add(InlineKeyboardButton("➕ إضافة وحدة جديدة", callback_data="add_hw_unit_prompt"))
    markup.add(InlineKeyboardButton("📌 عرض جميع الواجبات المفعلة الآن", callback_data="view_active_assignments"))
    return text, _back(markup)


def build_unit_homework_list(unit_name, unit_id, homeworks):
    markup = InlineKeyboardMarkup()
    text = (
        f"📝 **واجبات وحدة:** {esc(unit_name)}\n\n"
        "اضغط على الواجب لتبديل حالته بنقرة واحدة:\n"
        "🟢 **واجب الآن** (يستقبل حلول التلاميذ)\n"
        "🟡 **واجب سابق** (يغلق التسليم ويضع 00/20 لمن لم يسلم)\n"
        "🔴 **مغلق** (معطل ومخفي)\n"
    )
    for ex_id, ex_name, is_active in homeworks:
        if is_active == 1:
            status = "🟢 واجب الآن"
        elif is_active == 2:
            status = "🟡 منتهي (00/20)"
        else:
            status = "🔴 مغلق"

        markup.add(
            InlineKeyboardButton(f"{ex_name} [{status}]", callback_data=f"toggle_hw_status_{ex_id}"),
            InlineKeyboardButton("❌ حذف", callback_data=f"del_hw_item_{ex_id}")
        )
    markup.add(InlineKeyboardButton("➕ إضافة واجب منزلي جديد", callback_data=f"add_hw_prompt_{unit_id}"))
    markup.add(InlineKeyboardButton("🔙 رجوع لقائمة الواجبات", callback_data="manage_units_menu"))
    return text, markup


# ------------------- 9. تسيير مواضيع البكالوريا (BAC Archive) -------------------
def build_bac_admin_menu(topics):
    markup = InlineKeyboardMarkup()
    text = (
        f"📂 **إدارة مواضيع شهادة البكالوريا BAC ({len(topics)} موضوع مسجل):**\n"
        "مرتبة حسب السنة والشعبة:\n\n"
    )
    for topic_id, year, stream, title in topics[:25]:
        text += f"• 🎓 [{year}] *{esc(stream)}* ➔ {esc(title)}\n"
        markup.add(
            InlineKeyboardButton(f"📄 {year} - {title[:20]}", callback_data=f"view_bac_{topic_id}"),
            InlineKeyboardButton("❌ حذف", callback_data=f"del_bac_{topic_id}")
        )
    markup.add(InlineKeyboardButton("➕ إضافة موضوع بكالوريا جديد (PDF)", callback_data="add_bac_prompt"))
    return text, _back(markup)


# ------------------- 10. تسيير الملفات المتنوعة (نظام المجلدات Fichier) -------------------
def build_files_folders_admin(folders, files, current_folder_id=0):
    markup = InlineKeyboardMarkup()
    text = "📁 **نظام الملفات المتنوعة (Fichier Drive):**\n\n"

    if folders:
        text += "📂 **المجلدات الحالية:**\n"
        for f_id, f_name in folders:
            text += f"• 📁 {esc(f_name)}\n"
            markup.add(
                InlineKeyboardButton(f"📁 فتح {f_name}", callback_data=f"admin_open_folder_{f_id}"),
                InlineKeyboardButton("❌ حذف", callback_data=f"admin_del_folder_{f_id}")
            )

    if files:
        text += "\n📄 **الملفات:**\n"
        for file_id_db, title, f_type in files:
            icon = "🖼️ صورة" if f_type == "photo" else "📄 PDF"
            text += f"• {icon}: {esc(title)}\n"
            markup.add(
                InlineKeyboardButton(f"👁️ عرض {title}", callback_data=f"admin_view_file_{file_id_db}"),
                InlineKeyboardButton("❌ حذف", callback_data=f"admin_del_file_{file_id_db}")
            )

    markup.add(
        InlineKeyboardButton("➕ إنشاء مجلد جديد", callback_data=f"admin_create_folder_{current_folder_id}"),
        InlineKeyboardButton("➕ رفع ملف / صور", callback_data=f"admin_upload_file_{current_folder_id}")
    )
    if current_folder_id != 0:
        markup.add(InlineKeyboardButton("🔙 رجوع للمجلد الرئيسي", callback_data="admin_open_folder_0"))
    else:
        markup.add(InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_home"))

    return text, markup


# ------------------- 11. مركز إعدادات الإشعارات والتواصل -------------------
def build_notifications_settings():
    markup = InlineKeyboardMarkup()
    chat_status = "🟢 مفتوحة" if get_setting("chat_enabled") == "1" else "🔴 مغلقة"
    hw_notif = "🟢 مفعل" if get_setting("notify_homework") == "1" else "🔴 معطل"
    les_notif = "🟢 مفعل" if get_setting("notify_lessons") == "1" else "🔴 معطل"
    bac_notif = "🟢 مفعل" if get_setting("notify_bac") == "1" else "🔴 معطل"

    text = (
        "🔔 **مركز إعدادات الإشعارات والتواصل المباشر:**\n\n"
        f"• **محادثة التلاميذ معي:** [{chat_status}]\n"
        f"• **إشعار تلقائي عند إضافة واجب:** [{hw_notif}]\n"
        f"• **إشعار تلقائي عند إضافة درس/تمرين:** [{les_notif}]\n"
        f"• **إشعار تلقائي عند إضافة موضوع BAC:** [{bac_notif}]\n\n"
        "اضغط على أي خيار لتبديل تفعيله فوراً بنقرة واحدة:"
    )
    markup.add(
        InlineKeyboardButton(f"💬 محادثة التلاميذ: {chat_status}", callback_data="toggle_setting_chat_enabled"),
        InlineKeyboardButton(f"📝 إشعار الواجبات: {hw_notif}", callback_data="toggle_setting_notify_homework")
    )
    markup.add(
        InlineKeyboardButton(f"📘 إشعار الدروس: {les_notif}", callback_data="toggle_setting_notify_lessons"),
        InlineKeyboardButton(f"🎓 إشعار البكالوريا: {bac_notif}", callback_data="toggle_setting_notify_bac")
    )
    markup.add(InlineKeyboardButton("📢 إرسال إعلان عام فوري (Broadcast)", callback_data="broadcast_start"))
    return text, _back(markup)


# ------------------- 12. صندوق رسائل التلاميذ والمحادثات (Instagram Style) -------------------
def build_chat_inbox_menu(conversations):
    markup = InlineKeyboardMarkup()
    text = f"💬 **محادثات واستفسارات التلاميذ النشطة ({len(conversations)} تلميذ):**\n\n"
    if not conversations:
        return "📭 لا توجد محادثات أو رسائل جديدة من التلاميذ خلال آخر 24 ساعة.", _back(markup)

    for student_id, student_name, school, unread_cnt, last_msg, last_time in conversations:
        badge = f"🔴 ({unread_cnt} جديدة)" if unread_cnt > 0 else "👁️ مقروءة"
        btn_text = f"💬 {student_name} [{badge}]"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"open_chat_{student_id}"))

    return text, _back(markup)


def build_student_chat_view(student_id, student_name, messages):
    markup = InlineKeyboardMarkup()
    text = (
        f"💬 **محادثة إنستغرام مع التلميذ:** *{esc(student_name)}* (ID: `{student_id}`)\n"
        "⚠️ *تُحذف الرسائل تلقائياً بعد مرور 24 ساعة.*\n"
        "──────────────────────────────\n\n"
    )

    if not messages:
        text += "📭 لا توجد رسائل سابقة في آخر 24 ساعة.\n"
    else:
        for row in messages:
            sender_id, sender_name, msg_text, created_at = row[0], row[1], row[2], row[3]
            file_id_row = row[4] if len(row) > 4 else None
            file_type_row = row[5] if len(row) > 5 else None
            short_time = created_at.split()[-1][:5] if created_at else ""
            display_text = msg_text
            if file_id_row and file_type_row == "photo":
                display_text = f"[📷 صورة]"
            elif file_id_row and file_type_row == "document":
                display_text = f"[📄 ملف]"
            if sender_id != student_id:
                # رسالة الأستاذ (أنت ➔ يمين الشاشة)
                text += (
                    f"                                        👑 **أنت (الأستاذ)** 🕒 `{short_time}`\n"
                    f"                             ╭──────────────────\n"
                    f"                             │ 💬 {esc(display_text)}\n"
                    f"                             ╰──────────────────\n\n"
                )
            else:
                # رسالة التلميذ (الطرف الآخر ➔ يسار الشاشة)
                text += (
                    f"👤 **{esc(student_name)}** 🕒 `{short_time}`\n"
                    f"╭──────────────────\n"
                    f"│ 📩 _{esc(display_text)}_\n"
                    f"╰──────────────────\n\n"
                )

    text += "──────────────────────────────"
    markup.add(InlineKeyboardButton(f"✍️ إرسال رد / رسالة لـ {student_name}", callback_data=f"reply_chat_{student_id}"))
    markup.add(
        InlineKeyboardButton("🗑️ مسح المحادثة", callback_data=f"admin_clear_chat_{student_id}"),
        InlineKeyboardButton("🔄 تحديث", callback_data=f"open_chat_{student_id}")
    )
    markup.add(InlineKeyboardButton("🔙 رجوع لقائمة المحادثات", callback_data="admin_chat_inbox"))
    return text, markup


def build_user_days_list(user_id, full_name, dates):
    markup = InlineKeyboardMarkup()
    text = f"📅 *نشاط {esc(full_name)}* (ID: `{user_id}`)\n"
    text += f"🔥 **إجمالي أيام النشاط:** `{len(dates)}` يوم\n"
    text += "──────────────────\n"
    text += "\n".join(f"• {d}" for d in dates[:60]) if dates else "لا يوجد نشاط مسجّل بعد."
    return text, _back(markup, "users")
