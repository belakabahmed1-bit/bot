"""أدوات مشتركة: منع تكرار الأحداث، إرسال الملفات كألبوم، حماية Markdown، ومنع النقر المزدوج."""
import json
import threading
import time
from collections import OrderedDict

from telebot.apihelper import ApiTelegramException
from telebot.types import InputMediaDocument, InputMediaPhoto

_lock = threading.Lock()


# ===================== منع معالجة نفس التحديث مرتين =====================
def install_update_dedup(bot, keep=3000):
    """يتجاهل أي update_id وصل أكثر من مرة إلى هذه النسخة من البوت."""
    seen = OrderedDict()
    original = bot.process_new_updates

    def dedup(updates):
        fresh = []
        with _lock:
            for u in updates:
                uid = getattr(u, "update_id", None)
                if uid is None:
                    fresh.append(u)
                    continue
                if uid in seen:
                    continue
                seen[uid] = True
                fresh.append(u)
            while len(seen) > keep:
                seen.popitem(last=False)
        if fresh:
            original(fresh)

    bot.process_new_updates = dedup


# ===================== حماية الرسائل من أخطاء Markdown =====================
def install_safe_api(bot):
    """إذا فشل Markdown (رمز غير مغلق) يُعاد الإرسال كنص عادي بدل ضياع الرسالة.
    كما يتم تجاهل خطأ 'message is not modified' الناتج عن النقر المتكرر."""

    def wrap(name, ignore_not_modified=False):
        original = getattr(bot, name)

        def wrapper(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except ApiTelegramException as e:
                desc = str(e)
                if ignore_not_modified and "message is not modified" in desc:
                    return None
                if "can't parse entities" in desc and kwargs.get("parse_mode"):
                    kwargs = dict(kwargs)
                    kwargs["parse_mode"] = None
                    return original(*args, **kwargs)
                raise

        setattr(bot, name, wrapper)

    wrap("send_message")
    wrap("edit_message_text", ignore_not_modified=True)
    wrap("send_document")
    wrap("send_photo")


# ===================== منع النقر المزدوج =====================
_last_hit = {}


def throttle(key, seconds=3.0):
    """يرجع True إذا كان مسموحاً بالتنفيذ، و False إذا تكرر نفس الطلب خلال مدة قصيرة."""
    now = time.monotonic()
    with _lock:
        last = _last_hit.get(key)
        if last is not None and now - last < seconds:
            return False
        _last_hit[key] = now
        if len(_last_hit) > 5000:
            for k in [k for k, t in _last_hit.items() if now - t > 60]:
                _last_hit.pop(k, None)
    return True


# ===================== الألبومات الواردة =====================
_albums = {}


def first_in_album(message):
    """رسائل الألبوم تصل منفصلة؛ نرد مرة واحدة فقط على أول رسالة منها."""
    gid = getattr(message, "media_group_id", None)
    if not gid:
        return True
    key = (message.chat.id, gid)
    now = time.monotonic()
    with _lock:
        if key in _albums:
            return False
        _albums[key] = now
        if len(_albums) > 2000:
            for k in [k for k, t in _albums.items() if now - t > 300]:
                _albums.pop(k, None)
    return True


# ===================== إرسال الملفات =====================
def parse_media(extra_json, fallback_file_id=None):
    """يحوّل حقل extra_media إلى قائمة (النوع، file_id) بدون تكرار."""
    try:
        data = json.loads(extra_json) if extra_json else []
    except Exception:
        data = []

    items, seen = [], set()
    for m in data if isinstance(data, list) else []:
        if isinstance(m, dict) and m.get("file_id") and m["file_id"] not in seen:
            seen.add(m["file_id"])
            items.append((m.get("type") or "unknown", m["file_id"]))

    if not items and fallback_file_id:
        items.append(("unknown", fallback_file_id))
    return items


def _send_single(bot, chat_id, kind, file_id, caption, protect):
    kw = dict(caption=caption, parse_mode="Markdown" if caption else None, protect_content=protect)
    if kind == "photo":
        bot.send_photo(chat_id, file_id, **kw)
    elif kind == "document":
        bot.send_document(chat_id, file_id, **kw)
    else:  # نوع غير معروف (بيانات قديمة)
        try:
            bot.send_document(chat_id, file_id, **kw)
        except Exception:
            bot.send_photo(chat_id, file_id, **kw)


def _send_group(bot, chat_id, kind, ids, caption, protect):
    """يرسل مجموعة من نفس النوع كألبوم واحد (10 كحد أقصى لكل ألبوم)."""
    sent = 0
    for start in range(0, len(ids), 10):
        chunk = ids[start:start + 10]
        cap = caption if start == 0 else None

        if len(chunk) == 1:
            try:
                _send_single(bot, chat_id, kind, chunk[0], cap, protect)
                sent += 1
            except Exception as e:
                print("send single error:", e)
            continue

        media = []
        for i, fid in enumerate(chunk):
            c = cap if i == 0 else None
            if kind == "photo":
                media.append(InputMediaPhoto(media=fid, caption=c, parse_mode="Markdown" if c else None))
            else:
                media.append(InputMediaDocument(media=fid, caption=c, parse_mode="Markdown" if c else None))
        try:
            bot.send_media_group(chat_id, media, protect_content=protect)
            sent += len(chunk)
        except Exception as e:
            print("media group error, falling back to single sends:", e)
            for i, fid in enumerate(chunk):
                try:
                    _send_single(bot, chat_id, kind, fid, cap if i == 0 else None, protect)
                    sent += 1
                except Exception as e2:
                    print("send single error:", e2)
    return sent


def send_media_bundle(bot, chat_id, caption, items, protect=True):
    """يرسل كل صور وملفات المحتوى دفعة واحدة:
    ألبوم واحد للصور وألبوم واحد للملفات (تيليجرام لا يسمح بخلط الصور والملفات في ألبوم واحد).
    يرجع عدد العناصر المرسلة."""
    photos = [f for k, f in items if k == "photo"]
    docs = [f for k, f in items if k == "document"]
    unknown = [f for k, f in items if k not in ("photo", "document")]

    sent = 0
    first = True
    if docs:
        sent += _send_group(bot, chat_id, "document", docs, caption if first else None, protect)
        first = False
    if photos:
        sent += _send_group(bot, chat_id, "photo", photos, caption if first else None, protect)
        first = False
    for fid in unknown:
        try:
            _send_single(bot, chat_id, "unknown", fid, caption if first else None, protect)
            sent += 1
            first = False
        except Exception as e:
            print("send unknown error:", e)
    return sent
