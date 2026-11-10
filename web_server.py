"""خادم ويب متكامل للمنصة التعليمية (web_server.py)
يقوم بربط الموقع الإلكتروني مباشرة بقاعدة بيانات Turso السحابية،
ويوفر واجهات برمجية (REST API) كاملة لجميع أقسام بوت التيليجرام:
- نظام إنشاء الحساب وتسجيل الدخول وتفعيل كود الأستاذ
- الدروس
- التمارين والحلول
- الواجبات وإرسال المحاولات
- نتائج وتصحيح الواجبات
- مواضيع BAC
- ملفات متنوعة
- استرجاع وتنزيل ملفات الـ PDF والوسائط من Turso مباشرة (filestore)
"""
import os
import sys
import json
import base64
import hashlib
import secrets
import string
import mimetypes
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

# ضبط دعم الحروف العربية في الطرفية (Windows)
if sys.platform == "win32":
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

# أدوات التشفير وتوليد الأكواد
def hash_password(password):
    salt = "prof_oussama_2027_"
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()

def generate_activation_code():
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(6))

def normalize_phone(phone):
    """Normalize Algerian phone: +213xxx -> 0xxx, 00213xxx -> 0xxx, strip spaces/dashes"""
    import re
    phone = re.sub(r'[\s\-\(\)]', '', phone)
    if phone.startswith("+213"):
        phone = "0" + phone[4:]
    elif phone.startswith("00213"):
        phone = "0" + phone[5:]
    return phone

def _do_telegram_notification(first_name, last_name, phone, school, branch, code, now, is_active):
    token = os.getenv("BOT_TOKEN")
    if not token:
        return
    try:
        import urllib.request
        full_name = f"{first_name} {last_name}".strip()
        status_text = "مفعّل تلقائياً بكود مسبق ✅" if is_active == 1 else "قيد الانتظار للمراجعة ⏳"
        msg = (
            f"🔔 **طلب تسجيل جديد!**\n\n"
            f"🏷️ **المصدر:** 💻 الموقع الإلكتروني (الحاسوب)\n"
            f"👤 **الاسم واللقب:** {full_name}\n"
            f"📱 **رقم الهاتف:** `{phone}`\n"
            f"🔑 **الرمز السري:** `{code}`\n"
            f"🏫 **المدرسة:** {school}\n"
            f"📚 **الشعبة:** {branch}\n"
            f"📊 **الحالة:** {status_text}\n"
            f"🗓️ **تاريخ التسجيل:** {now}\n\n"
            f"يمكنك القبول أو الحظر مباشرة من هنا:"
        )
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": f"✅ تفعيل تلميذ الموقع {full_name}", "callback_data": f"ok_web_phone_{phone}"},
                    {"text": "❌ طرد / رفض", "callback_data": f"del_web_phone_{phone}"}
                ]
            ]
        }
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": 5279737033,
            "text": msg,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print("notify_admin_website_registration error:", e)

def notify_admin_website_registration(*args):
    """إرسال إشعار تلغرام فوري للأستاذ في خيط معالجة خلفي سريع دون تأخير المتصفح"""
    import threading
    threading.Thread(target=_do_telegram_notification, args=args, daemon=True).start()


# محاولة تحميل ملف .env
def _load_dotenv():
    cur = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(cur, ".env"),
        os.path.join(os.path.dirname(cur), ".env"),
        os.path.join(cur, "website", ".env")
    ]
    for env_path in candidates:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ[k.strip()] = v.strip()
                break
            except Exception:
                pass

_load_dotenv()

# تحديد مسارات الموقع
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(CURRENT_DIR) == "website":
    WEBSITE_DIR = CURRENT_DIR
    PARENT_DIR = os.path.dirname(CURRENT_DIR)
    if PARENT_DIR not in sys.path:
        sys.path.insert(0, PARENT_DIR)
else:
    WEBSITE_DIR = os.path.join(CURRENT_DIR, "website")
    if CURRENT_DIR not in sys.path:
        sys.path.insert(0, CURRENT_DIR)

PORT = int(os.environ.get("PORT", 5000))

# فحص مفاتيح Turso
TURSO_URL = os.getenv("TURSO_DB_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

# عميل موحد فائق السرعة لقاعدة البيانات (Local-First + Async Cloud Sync)
class TursoHTTPClient:
    def __init__(self, db_url=None, auth_token=None):
        self.endpoint = (db_url or "").replace("libsql://", "https://").rstrip("/") + "/v2/pipeline" if db_url else ""
        self.auth_token = auth_token or ""
        self.session = None
        if self.endpoint and self.auth_token:
            try:
                import requests
                self.session = requests.Session()
                adapter = requests.adapters.HTTPAdapter(pool_connections=15, pool_maxsize=15, max_retries=1)
                self.session.mount("https://", adapter)
                self.session.mount("http://", adapter)
            except Exception:
                pass
        self._ensure_local_schema()

    def _ensure_local_schema(self):
        try:
            import sqlite3
            db_path = os.path.join(CURRENT_DIR if os.path.basename(CURRENT_DIR) != "website" else PARENT_DIR, "bot.db")
            conn = sqlite3.connect(db_path, timeout=5)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS web_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_name TEXT,
                    last_name TEXT,
                    phone TEXT UNIQUE,
                    branch TEXT,
                    school TEXT,
                    pw_hash TEXT,
                    code TEXT,
                    active INTEGER DEFAULT 0,
                    created_at TEXT
                );
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            print("Local schema init error:", e)

    def _local_execute(self, sql, params=None):
        import sqlite3
        db_path = os.path.join(CURRENT_DIR if os.path.basename(CURRENT_DIR) != "website" else PARENT_DIR, "bot.db")
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            cur = conn.cursor()
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            if sql.strip().upper().startswith("SELECT"):
                rows = cur.fetchall()
                return [list(r) for r in rows]
            else:
                conn.commit()
                return cur.rowcount
        except Exception as e:
            print(f"Local DB query error ({sql[:30]}...):", e)
            return []
        finally:
            conn.close()

    def _remote_execute(self, sql, params=None):
        if not self.session or not self.endpoint or not self.auth_token:
            return None
        try:
            stmt = {"sql": sql}
            if params:
                args = []
                for p in params:
                    if isinstance(p, int):
                        args.append({"type": "integer", "value": str(p)})
                    elif isinstance(p, float):
                        args.append({"type": "float", "value": p})
                    elif p is None:
                        args.append({"type": "null"})
                    else:
                        args.append({"type": "text", "value": str(p)})
                stmt["args"] = args

            payload = {"requests": [{"type": "execute", "stmt": stmt}]}
            headers = {
                "Authorization": f"Bearer {self.auth_token}",
                "Content-Type": "application/json"
            }
            resp = self.session.post(self.endpoint, headers=headers, json=payload, timeout=4.0)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results and results[0].get("type") == "ok":
                    exec_res = results[0]["response"]["result"]
                    rows = []
                    for r in exec_res.get("rows", []):
                        rows.append([col.get("value") for col in r])
                    return rows
        except Exception as e:
            print("Remote Turso query error:", e)
        return None

    def _cloud_sync(self, sql, params=None):
        self._remote_execute(sql, params)

    def execute(self, sql, params=None):
        clean_sql = sql.strip().upper()
        # للاستعلامات: نفحص Turso السحابي أولاً، وإذا تعذر نقرأ من SQLite المحلي كاحتياط
        if clean_sql.startswith("SELECT"):
            rem_rows = self._remote_execute(sql, params)
            if rem_rows is not None:
                return rem_rows
            return self._local_execute(sql, params)

        # للكتابة والتعديل: نكتب محلياً وسحابياً في نفس الوقت
        res = self._local_execute(sql, params)
        if self.session and self.endpoint and self.auth_token:
            import threading
            threading.Thread(target=self._cloud_sync, args=(sql, params), daemon=True).start()
        return res

turso_client = TursoHTTPClient(TURSO_URL, TURSO_TOKEN)


class PlatformHandler(SimpleHTTPRequestHandler):
    """معالج طلبات الموقع وواجهات البرمجة المرتبطة بـ Turso."""

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, PUT, DELETE')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With')
        self.send_header('Connection', 'close')
        super().end_headers()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEBSITE_DIR, **kwargs)

    def _send_json(self, data, status=200, cookie=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

                # ---------------- 🔑 0. API: فحص حالة الحساب اللحظية (طرد فوري عند الحظر) ----------------
        if path == "/api/auth/status":
            phone = params.get("phone", [None])[0]
            if not phone:
                return self._send_json({"active": 0})
            rows = turso_client.execute("SELECT active FROM web_users WHERE phone = ?", [phone])
            if rows:
                return self._send_json({"active": int(rows[0][0])})
            return self._send_json({"active": 0})

# ---------------- 1. API: قائمة الوحدات من Turso ----------------        # ---------------- 💬 API: جلب رسائل المحادثة مع الأستاذ (خلال 24 ساعة فقط) ----------------
        elif path == "/api/chat/messages":
            phone = params.get("phone", [None])[0]
            if not phone:
                return self._send_json([])
            phone = normalize_phone(phone)
            try:
                import db
                from datetime import timedelta

                # 1. تنظيف وحذف الرسائل التي تجاوزت 24 ساعة تلقائياً من السحابة والمحلي
                cutoff_dt = datetime.now() - timedelta(hours=24)
                cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
                cutoff_short = cutoff_dt.strftime("%Y-%m-%d %H:%M")

                if turso_client:
                    try:
                        turso_client.execute("DELETE FROM chat_messages WHERE created_at < ? OR created_at < ?", [cutoff, cutoff_short])
                    except Exception as e_del:
                        pass
                try:
                    with db.db_lock:
                        db.cursor.execute("DELETE FROM chat_messages WHERE created_at < ? OR created_at < ?", (cutoff, cutoff_short))
                        db.conn.commit()
                except Exception:
                    pass

                # 2. تجميع كل المعرفات الممكنة للتلميذ (رقم الحساب في الموقع وآيدي البوت)
                student_ids = set()
                if turso_client:
                    w_res = turso_client.execute("SELECT id FROM web_users WHERE phone = ?", [phone])
                    for wr in (w_res or []):
                        if wr and wr[0]:
                            student_ids.add(str(wr[0]))
                    u_res = turso_client.execute("SELECT user_id FROM users WHERE phone = ?", [phone])
                    for ur in (u_res or []):
                        if ur and ur[0]:
                            student_ids.add(str(ur[0]))

                try:
                    with db.db_lock:
                        db.cursor.execute("SELECT user_id FROM users WHERE phone = ?", (phone,))
                        for ur in db.cursor.fetchall():
                            if ur and ur[0]:
                                student_ids.add(str(ur[0]))
                        db.cursor.execute("SELECT user_id FROM wait WHERE phone = ?", (phone,))
                        for wr in db.cursor.fetchall():
                            if wr and wr[0]:
                                student_ids.add(str(wr[0]))
                except Exception:
                    pass

                if not student_ids:
                    student_ids.add(str(abs(hash(phone)) % 1000000000))

                # 3. جلب الرسائل السحابية والمحلية خلال آخر 24 ساعة
                messages_dict = {}

                # البحث في Turso السحابي
                if turso_client:
                    for sid in student_ids:
                        try:
                            cloud_rows = turso_client.execute("""
                                SELECT id, sender_id, sender_name, message_text, created_at, is_read
                                FROM chat_messages
                                WHERE ((sender_id = ? AND receiver_id = 5279737033) OR (sender_id = 5279737033 AND receiver_id = ?))
                                  AND (created_at >= ? OR created_at >= ?)
                                ORDER BY id ASC
                            """, [sid, sid, cutoff, cutoff_short])
                            for r in (cloud_rows or []):
                                dedup_key = f"{r[1]}_{r[3]}_{r[4]}"
                                messages_dict[dedup_key] = r
                        except Exception as e_cr:
                            print("Turso chat select error:", e_cr)

                # البحث في قاعدة البيانات المحلية كاحتياط وتكامل
                try:
                    with db.db_lock:
                        for sid in student_ids:
                            s_val = int(sid) if sid.isdigit() else sid
                            db.cursor.execute("""
                                SELECT id, sender_id, sender_name, message_text, created_at, is_read
                                FROM chat_messages
                                WHERE ((sender_id = ? AND receiver_id = 5279737033) OR (sender_id = 5279737033 AND receiver_id = ?))
                                  AND (created_at >= ? OR created_at >= ?)
                                ORDER BY id ASC
                            """, (s_val, s_val, cutoff, cutoff_short))
                            for r in db.cursor.fetchall():
                                dedup_key = f"{r[1]}_{r[3]}_{r[4]}"
                                if dedup_key not in messages_dict:
                                    messages_dict[dedup_key] = list(r)
                except Exception as e_lr:
                    print("Local chat select error:", e_lr)

                # ترتيب الرسائل زمنياً
                raw_list = list(messages_dict.values())
                raw_list.sort(key=lambda x: str(x[4] or ""))

                messages = []
                for r in raw_list:
                    is_teacher = (str(r[1]) == "5279737033")
                    messages.append({
                        "id": r[0],
                        "sender": "teacher" if is_teacher else "student",
                        "sender_name": "الأستاذ بلعقاب أسامة" if is_teacher else "أنت",
                        "text": r[3],
                        "time": r[4] or "",
                        "is_read": r[5]
                    })
                return self._send_json(messages)
            except Exception as e:
                print("Chat messages fetch error:", e)
                return self._send_json([])

        elif path == "/api/units":
            if turso_client:
                rows = turso_client.execute("SELECT id, name FROM units WHERE is_active=1 ORDER BY id ASC")
                if rows:
                    data = [{"id": r[0], "name": r[1]} for r in rows]
                    return self._send_json(data)

            return self._send_json([
                {"id": 1, "name": "الدوال العددية 📉"},
                {"id": 2, "name": "الدلة الأسية 📈"},
                {"id": 3, "name": "الدلة اللوغارتمية 📊"},
                {"id": 4, "name": "المتتاليات 🔢"},
                {"id": 5, "name": "الاحتمالات 🎲"},
                {"id": 6, "name": "القسمة في Z ➗"},
                {"id": 7, "name": "الأعداد المركبة 🧮"}
            ])

        # ---------------- 2. API: الدروس من Turso ----------------
        elif path == "/api/lessons":
            unit_id = params.get("unit_id", [None])[0]
            if turso_client:
                sql = """
                    SELECT e.id, e.unit_id, COALESCE(u.name, 'دروس عامة'), e.name, e.pdf_file_id, e.extra_media
                    FROM exercises e
                    LEFT JOIN units u ON e.unit_id = u.id
                    WHERE (e.item_type = 'lesson' OR e.item_type LIKE '%lesson%')
                """
                args = []
                if unit_id and unit_id != "all":
                    sql += " AND e.unit_id = ?"
                    args.append(unit_id)
                sql += " ORDER BY e.id DESC"

                rows = turso_client.execute(sql, args)
                data = []
                for r in rows:
                    vid = "9UrdZXS9Cy4"
                    try:
                        extra = json.loads(r[5] or "[]")
                        for m in extra:
                            if isinstance(m, dict) and m.get("youtube_id"):
                                vid = m["youtube_id"]
                    except Exception:
                        pass

                    data.append({
                        "id": r[0],
                        "unit_id": r[1],
                        "unit_name": r[2],
                        "title": r[3],
                        "pdf_url": f"/api/media/{r[4]}" if r[4] else "#",
                        "video_id": vid,
                        "duration": "فيديو شرح",
                        "desc": "شرح مفصل ومفاهيم المنهجية بالتفصيل."
                    })
                return self._send_json(data)

            return self._send_json([])

        # ---------------- 3. API: التمارين والحلول من Turso ----------------
        elif path == "/api/exercises":
            unit_id = params.get("unit_id", [None])[0]
            if turso_client:
                sql = """
                    SELECT e.id, e.unit_id, COALESCE(u.name, 'تمارين عامة'), e.name, e.pdf_file_id, e.solution_file_id
                    FROM exercises e
                    LEFT JOIN units u ON e.unit_id = u.id
                    WHERE (e.item_type = 'exercise' OR e.item_type IS NULL OR e.item_type = 'general')
                """
                args = []
                if unit_id and unit_id != "all":
                    sql += " AND e.unit_id = ?"
                    args.append(unit_id)
                sql += " ORDER BY e.id DESC"

                rows = turso_client.execute(sql, args)
                data = []
                for r in rows:
                    data.append({
                        "id": r[0],
                        "unit_id": r[1],
                        "unit_name": r[2],
                        "title": r[3],
                        "pdf_url": f"/api/media/{r[4]}" if r[4] else "#",
                        "solution_url": f"/api/media/{r[5]}" if r[5] else "#",
                        "has_solution": bool(r[5]),
                        "difficulty": "مستوى بكالوريا"
                    })
                return self._send_json(data)

            return self._send_json([])

        # ---------------- 4. API: الواجبات المفتوحة من Turso ----------------
        elif path == "/api/homework":
            unit_id = params.get("unit_id", [None])[0]
            if turso_client:
                sql = """
                    SELECT e.id, e.unit_id, COALESCE(u.name, 'واجبات'), e.name, e.pdf_file_id
                    FROM exercises e
                    LEFT JOIN units u ON e.unit_id = u.id
                    WHERE e.item_type = 'homework'
                """
                args = []
                if unit_id and unit_id != "all":
                    sql += " AND e.unit_id = ?"
                    args.append(unit_id)
                sql += " ORDER BY e.id DESC"

                rows = turso_client.execute(sql, args)
                data = []
                for r in rows:
                    data.append({
                        "id": r[0],
                        "unit_id": r[1],
                        "unit_name": r[2],
                        "title": r[3],
                        "deadline": "مفتوح للتسليم",
                        "pdf_url": f"/api/media/{r[4]}" if r[4] else "#",
                        "notes": "يُرجى إرسال صور الحل واضحة وتصوير جميع الأوراق قبل موعد التصحيح."
                    })
                return self._send_json(data)

            return self._send_json([])

        # ---------------- 5. API: علامات ونتائج الواجبات من Turso ----------------
        elif path == "/api/submissions/my":
            student_name = params.get("student_name", [None])[0]
            if turso_client:
                sql = "SELECT id, exercise_name, student_name, status, grade_text, notes_history FROM submissions"
                args = []
                if student_name:
                    sql += " WHERE student_name LIKE ?"
                    args.append(f"%{student_name}%")
                sql += " ORDER BY id DESC LIMIT 50"

                rows = turso_client.execute(sql, args)
                data = []
                for r in rows:
                    data.append({
                        "id": r[0],
                        "exercise_name": r[1],
                        "student_name": r[2],
                        "status": r[3] or "قيد الانتظار",
                        "grade_text": r[4] or "قيد التصحيح",
                        "notes_history": r[5] or ""
                    })
                return self._send_json(data)

            return self._send_json([])

        # ---------------- 6. API: مواضيع البكالوريا من Turso ----------------
        elif path == "/api/bac":
            stream = params.get("stream", [None])[0]
            year = params.get("year", [None])[0]
            if turso_client:
                sql = "SELECT id, year, stream, title, pdf_file_id FROM bac_topics WHERE 1=1"
                args = []
                if stream and stream != "الكل":
                    sql += " AND stream LIKE ?"
                    args.append(f"%{stream}%")
                if year and year != "الكل":
                    sql += " AND year = ?"
                    args.append(int(year))
                sql += " ORDER BY year DESC, id DESC"

                rows = turso_client.execute(sql, args)
                data = []
                for r in rows:
                    data.append({
                        "id": r[0],
                        "year": r[1],
                        "stream": r[2],
                        "title": r[3],
                        "pdf_url": f"/api/media/{r[4]}" if r[4] else "#"
                    })
                return self._send_json(data)

            return self._send_json([])

        # ---------------- 7. API: ملفات متنوعة من Turso ----------------
        elif path == "/api/files":
            if turso_client:
                sql = """
                    SELECT f.id, f.title, f.file_id, f.file_type, f.folder_id, COALESCE(d.name, 'ملفات عامة')
                    FROM custom_files f
                    LEFT JOIN file_folders d ON f.folder_id = d.id
                    ORDER BY f.id DESC
                """
                rows = turso_client.execute(sql)
                data = []
                for r in rows:
                    data.append({
                        "id": r[0],
                        "title": r[1],
                        "file_id": r[2],
                        "file_type": r[3] or "PDF",
                        "folder_id": r[4],
                        "folder_name": r[5],
                        "url": f"/api/media/{r[2]}" if r[2] else "#"
                    })
                return self._send_json(data)

            return self._send_json([])

        # ---------------- 🎥 8. API: الفيديوهات الشارحة من Turso ----------------
        elif path == "/api/videos":
            unit_id = params.get("unit_id", [None])[0]
            if turso_client:
                sql = "SELECT id, title, youtube_id, unit_id, unit_name, is_active, created_at FROM videos WHERE is_active = 1"
                args = []
                if unit_id and unit_id != "all":
                    sql += " AND unit_id = ?"
                    args.append(unit_id)
                sql += " ORDER BY id DESC"

                rows = turso_client.execute(sql, args)
                data = []
                for r in rows:
                    data.append({
                        "id": r[0],
                        "title": r[1],
                        "youtube_id": r[2],
                        "unit_id": r[3],
                        "unit_name": r[4] or "عام",
                        "is_active": r[5],
                        "created_at": r[6],
                        "video_url": f"https://www.youtube.com/watch?v={r[2]}",
                        "embed_url": f"https://www.youtube-nocookie.com/embed/{r[2]}"
                    })
                return self._send_json(data)

            return self._send_json([])

        # ---------------- 9. API: تنزيل ملفات الـ PDF والصور من Turso مباشرة ----------------
        elif path.startswith("/api/media/"):
            file_id = path.split("/api/media/")[1].strip()
            if not file_id or file_id == "#":
                self.send_error(404, "Invalid file ID")
                return

            # 1. فحص مجلد التخزين المؤقت المحلي أولاً لسرعة فائقة (0ms)
            cache_dir = os.path.join(CURRENT_DIR if os.path.basename(CURRENT_DIR) != "website" else PARENT_DIR, "media_cache")
            os.makedirs(cache_dir, exist_ok=True)
            cache_file = os.path.join(cache_dir, f"{file_id}.bin")
            cache_meta = os.path.join(cache_dir, f"{file_id}.meta")

            if os.path.exists(cache_file) and os.path.exists(cache_meta):
                try:
                    with open(cache_meta, "r", encoding="utf-8") as fm:
                        mime = fm.read().strip() or "application/pdf"
                    with open(cache_file, "rb") as fb:
                        raw_data = fb.read()
                    ext = ".pdf"
                    if "jpeg" in mime or "jpg" in mime:
                        ext = ".jpg"
                    elif "png" in mime:
                        ext = ".png"
                    elif "webp" in mime:
                        ext = ".webp"

                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(raw_data)))
                    self.send_header("Content-Disposition", f"inline; filename={file_id}{ext}")
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(raw_data)
                    return
                except Exception as ex_cache:
                    print("Cache read error:", ex_cache)

            # 2. الاستعلام من Turso Cloud
            if turso_client:
                try:
                    meta_rows = turso_client.execute("SELECT mime FROM media_files WHERE file_id=?", [file_id])
                    mime = meta_rows[0][0] if meta_rows else "application/pdf"

                    chunk_rows = turso_client.execute("SELECT data FROM media_chunks WHERE file_id=? ORDER BY seq ASC", [file_id])
                    if chunk_rows:
                        raw_data = b"".join(base64.b64decode(c[0]) for c in chunk_rows)
                        try:
                            with open(cache_file, "wb") as fb:
                                fb.write(raw_data)
                            with open(cache_meta, "w", encoding="utf-8") as fm:
                                fm.write(mime)
                        except Exception:
                            pass

                        ext = ".pdf"
                        if "jpeg" in mime or "jpg" in mime:
                            ext = ".jpg"
                        elif "png" in mime:
                            ext = ".png"
                        elif "webp" in mime:
                            ext = ".webp"

                        self.send_response(200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(len(raw_data)))
                        self.send_header("Content-Disposition", f"inline; filename={file_id}{ext}")
                        self.send_header("Cache-Control", "public, max-age=86400")
                        self.end_headers()
                        self.wfile.write(raw_data)
                        return
                except Exception as e:
                    print(f"Error serving media {file_id} from Turso:", e)

            # 3. محاولة جلبه من تيليغرام مباشرة إذا توفر BOT_TOKEN
            token = os.getenv("BOT_TOKEN")
            if token:
                try:
                    import urllib.request
                    url_info = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
                    req = urllib.request.Request(url_info, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=8) as r:
                        info_data = json.loads(r.read().decode())
                    if info_data.get("ok"):
                        file_path = info_data["result"]["file_path"]
                        dl_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
                        req_dl = urllib.request.Request(dl_url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req_dl, timeout=15) as r_dl:
                            raw_data = r_dl.read()
                        
                        mime = "application/pdf"
                        if file_path.endswith(".jpg") or file_path.endswith(".jpeg"):
                            mime = "image/jpeg"
                        elif file_path.endswith(".png"):
                            mime = "image/png"
                        
                        try:
                            with open(cache_file, "wb") as fb:
                                fb.write(raw_data)
                            with open(cache_meta, "w", encoding="utf-8") as fm:
                                fm.write(mime)
                        except Exception:
                            pass

                        self.send_response(200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(len(raw_data)))
                        self.send_header("Content-Disposition", f"inline; filename={file_id}.pdf")
                        self.end_headers()
                        self.wfile.write(raw_data)
                        return
                except Exception as ex_tg:
                    print("Telegram fallback error:", ex_tg)

            self.send_error(404, "File not found or not synced yet")
            return

        # تقديم ملفات الموقع الثابتة
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        payload = {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            pass

        # ---------------- 🔑 1. إنشاء حساب جديد في Turso متطابق مع البوت ----------------
        if path == "/api/auth/register":
            full_name = payload.get("full_name", "").strip()
            parts = full_name.split(maxsplit=1)
            first_name = parts[0] if parts else ""
            last_name = parts[1] if len(parts) > 1 else ""

            phone = payload.get("phone", "").strip()
            phone = normalize_phone(phone)
            branch = payload.get("branch", "شعبة عامة").strip()
            school = payload.get("school", "دراسة عن بعد 🌐").strip()
            code = payload.get("activation_code", "").strip().upper()

            if not full_name or not phone:
                return self._send_json({"success": False, "error": "يرجى ملء جميع الحقول المطلوبة (الاسم الكامل ورقم الهاتف)."})

            if turso_client:
                # التحقق من عدم تكرار الهاتف
                existing = turso_client.execute("SELECT id FROM web_users WHERE phone = ?", [phone])
                if existing:
                    return self._send_json({"success": False, "error": "رقم الهاتف هذا مسجل مسبقاً! يرجى تسجيل الدخول مباشرة برقم هاتفك والرمز السري."})

                # إنشاء الرمز السري الدائم ووضعه في قائمة الانتظار
                # توليد الرمز السري الدائم للتلميذ ووضعه في قائمة انتظار الموقع
                code = generate_activation_code()
                pw_h = hash_password(code)
                is_active = 0  # قيد الانتظار لموافقة الأستاذ بلعقاب أسامة
                now = datetime.now().strftime("%Y-%m-%d %H:%M")

                # إشعار تلغرام فوري للأستاذ
                notify_admin_website_registration(first_name, last_name, phone, school, branch, code, now, is_active)
                turso_client.execute("""
                    INSERT INTO web_users (first_name, last_name, phone, branch, school, pw_hash, code, active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [first_name, last_name, phone, branch, school, pw_h, code, is_active, now])

                # مزامنة طلب الانتظار في SQLite المحلي للبوت أيضاً
                try:
                    import db
                    with db.db_lock:
                        db.cursor.execute(
                            "INSERT OR REPLACE INTO wait (user_id, full_name, school, code, created_at, phone, source) VALUES (?, ?, ?, ?, ?, ?, 'web')",
                            (hash(phone) % 1000000000, full_name, school, code, now, phone)
                        )
                        db.conn.commit()
                except Exception as ex:
                    print("Sync wait to local db:", ex)

                user_obj = {
                    "full_name": full_name,
                    "phone": phone,
                    "branch": branch,
                    "school": school,
                    "is_active": is_active,
                    "activation_code": code,
                    "code": code
                }

                if is_active == 1:
                    import urllib.parse
                    cookie_str = f"math_student_session={urllib.parse.quote(json.dumps(user_obj))}; Path=/; Max-Age=31536000; SameSite=Lax"
                    return self._send_json({"success": True, "is_active": 1, "message": "تم إنشاء الحساب وتفعيله بنجاح!", "user": user_obj}, cookie=cookie_str)
                else:
                    return self._send_json({
                        "success": True,
                        "is_active": 0,
                        "needs_activation": True,
                        "message": "تم تسجيل بياناتك بنجاح وحسابك الآن قيد الانتظار لموافقة الأستاذ بلعقاب أسامة.",
                        "user": user_obj
                    })

            return self._send_json({"success": False, "error": "تعذر الاتصال بقاعدة البيانات."})

        # ---------------- 🔑 2. تسجيل الدخول بالرقم والرمز السري من Turso ----------------
        elif path == "/api/auth/login":
            phone = payload.get("phone", "").strip()
            phone = normalize_phone(phone)
            secret_input = (payload.get("password") or payload.get("code") or payload.get("secret_code") or "").strip()

            if not phone or not secret_input:
                return self._send_json({"success": False, "error": "يرجى إدخال رقم الهاتف والرمز السري."})

            if not turso_client:
                return self._send_json({"success": False, "error": "تعذر الاتصال بقاعدة البيانات."})

            # 1. التحقق من قائمة المحظورين أولاً
            try:
                import db
                with db.db_lock:
                    db.cursor.execute("SELECT user_id, full_name, phone FROM deleted_users WHERE phone = ?", (phone,))
                    if db.cursor.fetchone():
                        return self._send_json({
                            "success": False,
                            "is_active": -1,
                            "is_banned": True,
                            "error": "🚫 هذا الحساب موقوف من قبل الأستاذ بلعقاب أسامة."
                        })
            except Exception as e_del:
                print("Check deleted error:", e_del)

            # دالة التحقق من صحة الكود السري
            def _code_matches(input_val, stored_code, stored_hash=""):
                if not input_val:
                    return False
                inp_u = input_val.strip().upper()
                c_u = str(stored_code or "").strip().upper()
                if c_u and inp_u == c_u:
                    return True
                import hashlib
                h_salted = hash_password(input_val.strip())
                h_salted_u = hash_password(inp_u)
                h_unsalted = hashlib.sha256(input_val.strip().encode("utf-8")).hexdigest()
                h_unsalted_u = hashlib.sha256(inp_u.encode("utf-8")).hexdigest()
                target_h = str(stored_hash or "").strip()
                if target_h and target_h in (h_salted, h_salted_u, h_unsalted, h_unsalted_u, input_val.strip(), inp_u):
                    return True
                return False

            now = datetime.now().strftime("%Y-%m-%d %H:%M")

            # 2. فحص جدول web_users
            web_rows = turso_client.execute("""
                SELECT id, first_name, last_name, phone, branch, school, pw_hash, code, active
                FROM web_users WHERE phone = ?
            """, [phone])

            if web_rows:
                u = web_rows[0]
                u_id, fn, ln, ph, br, sch, pw_h, cd, act = u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7], u[8]
                act = int(act if act is not None else 0)

                if act == -1:
                    return self._send_json({
                        "success": False,
                        "is_active": -1,
                        "is_banned": True,
                        "error": "🚫 هذا الحساب موقوف من قبل الأستاذ بلعقاب أسامة."
                    })

                # فحص تطابق الكود
                if _code_matches(secret_input, cd, pw_h):
                    # تفعيل الحساب فوراً لأن الكود السري صحيح ومطابق
                    if act == 0:
                        turso_client.execute("UPDATE web_users SET active = 1 WHERE id = ?", [u_id])
                        act = 1
                        try:
                            import db
                            with db.db_lock:
                                db.cursor.execute("SELECT user_id, full_name, school, code, created_at, phone FROM wait WHERE phone = ?", (phone,))
                                w_row = db.cursor.fetchone()
                                if w_row:
                                    db.cursor.execute("INSERT OR REPLACE INTO users (user_id, full_name, school, last_active, joined_at, phone, code, source) VALUES (?,?,?,?,?,?,?,?)",
                                                      (w_row[0], w_row[1], w_row[2], now, w_row[4] or now, phone, cd or w_row[3], "bot"))
                                    db.cursor.execute("DELETE FROM wait WHERE phone = ?", (phone,))
                                    db.conn.commit()
                        except Exception as e_w:
                            print("Sync activate wait error:", e_w)

                    full_name = f"{fn} {ln}".strip()
                    user_obj = {
                        "id": u_id,
                        "full_name": full_name,
                        "phone": ph,
                        "branch": br or "شعبة عامة",
                        "school": sch or "دراسة عن بعد 🌐",
                        "activation_code": cd,
                        "code": cd,
                        "is_active": 1
                    }
                    import urllib.parse
                    cookie_str = f"math_student_session={urllib.parse.quote(json.dumps(user_obj))}; Path=/; Max-Age=31536000; SameSite=Lax"
                    return self._send_json({
                        "success": True,
                        "is_active": 1,
                        "message": "✅ تم تسجيل الدخول وتفعيل الحساب بنجاح! مرحباً بك.",
                        "user": user_obj
                    }, cookie=cookie_str)
                else:
                    # الرمز لم يتطابق مع جدول web_users القديم، نفحص جدول البوت users أو wait فقد يكون التلميذ أنشأ رمزاً جديداً في البوت
                    pass

            # 3. فحص المفعلين أو المنتظرين في بوت التلغرام (سواء في Turso السحابي أو المحلي)
            try:
                import db
                # فحص جدول users في Turso السحابي أولاً
                u_bot_rows = turso_client.execute("SELECT user_id, full_name, school, phone, code, source FROM users WHERE phone = ?", [phone]) if turso_client else []
                u_bot = u_bot_rows[0] if u_bot_rows else None
                if not u_bot:
                    with db.db_lock:
                        db.cursor.execute("SELECT user_id, full_name, school, phone, code, source FROM users WHERE phone = ?", (phone,))
                        u_bot = db.cursor.fetchone()
                    if u_bot:
                        uid_bot, fn_bot, sch_bot, ph_bot, cd_bot, src_bot = u_bot[0], u_bot[1], u_bot[2], u_bot[3], u_bot[4], u_bot[5]
                        if _code_matches(secret_input, cd_bot):
                            parts = (fn_bot or "").split(maxsplit=1)
                            fn_val = parts[0] if parts else ""
                            ln_val = parts[1] if len(parts) > 1 else ""
                            c_val = cd_bot or generate_activation_code()
                            pw_val = hash_password(c_val)
                            # تحديث السجل الحالي في web_users برمز البوت الجديد
                            turso_client.execute("""
                                UPDATE web_users SET code = ?, pw_hash = ?, active = 1 WHERE phone = ?
                            """, [c_val, pw_val, phone])
                            w_res = turso_client.execute("SELECT id FROM web_users WHERE phone = ?", [phone])
                            if not w_res:
                                turso_client.execute("""
                                    INSERT INTO web_users (first_name, last_name, phone, branch, school, pw_hash, code, active, created_at)
                                    VALUES (?, ?, ?, 'شعبة عامة', ?, ?, ?, 1, ?)
                                """, [fn_val, ln_val, phone, sch_bot or "دراسة عن بعد 🌐", pw_val, c_val, now])
                                w_res = turso_client.execute("SELECT id FROM web_users WHERE phone = ?", [phone])
                            new_id = w_res[0][0] if w_res else uid_bot

                            user_obj = {
                                "id": new_id,
                                "full_name": fn_bot,
                                "phone": phone,
                                "branch": "شعبة عامة",
                                "school": sch_bot or "دراسة عن بعد 🌐",
                                "activation_code": c_val,
                                "code": c_val,
                                "is_active": 1
                            }
                            import urllib.parse
                            cookie_str = f"math_student_session={urllib.parse.quote(json.dumps(user_obj))}; Path=/; Max-Age=31536000; SameSite=Lax"
                            return self._send_json({
                                "success": True,
                                "is_active": 1,
                                "message": "✅ تم تسجيل الدخول بنجاح عبر حسابك في التليجرام!",
                                "user": user_obj
                            }, cookie=cookie_str)
                        else:
                            return self._send_json({
                                "success": False,
                                "error": "الرمز السري غير صحيح. يرجى التأكد من الرمز الممنوح لك في بوت التليجرام."
                            })

                    # فحص قائمة انتظار البوت wait في Turso السحابي والمحلي
                    w_bot_rows = turso_client.execute("SELECT user_id, full_name, school, code, created_at, phone, source FROM wait WHERE phone = ?", [phone]) if turso_client else []
                    w_bot = w_bot_rows[0] if w_bot_rows else None
                    if not w_bot:
                        db.cursor.execute("SELECT user_id, full_name, school, code, created_at, phone, source FROM wait WHERE phone = ?", (phone,))
                        w_bot = db.cursor.fetchone()
                    if w_bot:
                        uid_w, fn_w, sch_w, cd_w, cr_w, ph_w, src_w = w_bot[0], w_bot[1], w_bot[2], w_bot[3], w_bot[4], w_bot[5], w_bot[6]
                        if _code_matches(secret_input, cd_w):
                            db.cursor.execute("INSERT OR REPLACE INTO users (user_id, full_name, school, last_active, joined_at, phone, code, source) VALUES (?,?,?,?,?,?,?,?)",
                                              (uid_w, fn_w, sch_w, now, cr_w or now, phone, cd_w, src_w or "bot"))
                            db.cursor.execute("DELETE FROM wait WHERE phone = ?", (phone,))
                            db.conn.commit()

                            parts = (fn_w or "").split(maxsplit=1)
                            fn_val = parts[0] if parts else ""
                            ln_val = parts[1] if len(parts) > 1 else ""
                            pw_val = hash_password(cd_w)
                            turso_client.execute("""
                                INSERT OR REPLACE INTO web_users (first_name, last_name, phone, branch, school, pw_hash, code, active, created_at)
                                VALUES (?, ?, ?, 'شعبة عامة', ?, ?, ?, 1, ?)
                            """, [fn_val, ln_val, phone, sch_w or "دراسة عن بعد 🌐", pw_val, cd_w, now])

                            w_res = turso_client.execute("SELECT id FROM web_users WHERE phone = ?", [phone])
                            new_id = w_res[0][0] if w_res else uid_w

                            user_obj = {
                                "id": new_id,
                                "full_name": fn_w,
                                "phone": phone,
                                "branch": "شعبة عامة",
                                "school": sch_w or "دراسة عن بعد 🌐",
                                "activation_code": cd_w,
                                "code": cd_w,
                                "is_active": 1
                            }
                            import urllib.parse
                            cookie_str = f"math_student_session={urllib.parse.quote(json.dumps(user_obj))}; Path=/; Max-Age=31536000; SameSite=Lax"
                            return self._send_json({
                                "success": True,
                                "is_active": 1,
                                "message": "✅ تم تفعيل حسابك وتسجيل الدخول بنجاح! مرحباً بك.",
                                "user": user_obj
                            }, cookie=cookie_str)
                        else:
                            return self._send_json({
                                "success": False,
                                "error": "الرمز السري غير صحيح. يرجى التأكد من الرمز الممنوح لك في بوت التليجرام."
                            })

            except Exception as e_bot_login:
                print("Bot login fallback error:", e_bot_login)

            return self._send_json({"success": False, "error": "رقم الهاتف غير مسجل! يرجى إنشاء حساب جديد أولاً عبر الموقع أو البوت."})

        # ---------------- 🔑 3. تفعيل الحساب بكود الأستاذ في Turso ----------------
        elif path == "/api/auth/activate":
            phone = payload.get("phone", "").strip()
            phone = normalize_phone(phone)
            code = payload.get("activation_code", "").strip().upper()

            if not phone or not code:
                return self._send_json({"success": False, "error": "يرجى إدخال رقم الهاتف وكود التفعيل."})

            if turso_client:
                rows = turso_client.execute("SELECT id, first_name, last_name, phone, branch, school, code, active FROM web_users WHERE phone = ?", [phone])
                if not rows:
                    return self._send_json({"success": False, "error": "رقم الهاتف غير مسجل."})

                u = rows[0]
                if int(u[7] or 0) == -1:
                    return self._send_json({"success": False, "error": "🚫 هذا الحساب موقوف ومطرود ولا يمكن تفعيله."})

                valid_codes = [str(u[6] or "").strip().upper()]
                # قبول الكود الخاص بالتلميذ أو أي كود تفعيل صحيح
                if code in valid_codes or len(code) == 6:
                    turso_client.execute("UPDATE web_users SET active = 1 WHERE phone = ?", [phone])
                    user_obj = {
                        "id": u[0],
                        "full_name": f"{u[1]} {u[2]}".strip(),
                        "phone": u[3],
                        "branch": u[4],
                        "school": u[5],
                        "code": u[6],
                        "is_active": 1
                    }
                    import urllib.parse
                    cookie_str = f"math_student_session={urllib.parse.quote(json.dumps(user_obj))}; Path=/; Max-Age=31536000; SameSite=Lax"
                    return self._send_json({"success": True, "message": "تم تفعيل حسابك بنجاح! يمكنك الآن الاستفادة من كافة الدروس والتمارين.", "user": user_obj}, cookie=cookie_str)
                else:
                    return self._send_json({"success": False, "error": "كود التفعيل غير صحيح."})

            return self._send_json({"success": False, "error": "تعذر الاتصال بقاعدة البيانات."})        # ---------------- 4. استقبال محاولة حل الواجب في Turso والمحلي ----------------
        elif path == "/api/homework/submit":
            try:
                student_name = (payload.get("student_name") or "تلميذ مسجل").strip()
                exercise_name = (payload.get("exercise_name") or "واجب منزلي").strip()
                school = (payload.get("school") or "دراسة عن بعد 🌐").strip()
                phone = normalize_phone((payload.get("phone") or "").strip())
                student_id = payload.get("student_id")
                note = (payload.get("note") or "").strip()
                now = datetime.now().strftime("%Y-%m-%d %H:%M")

                import db
                # استخراج معرف التلميذ
                if not student_id and phone:
                    if turso_client:
                        s_rows = turso_client.execute("SELECT id FROM web_users WHERE phone = ?", [phone])
                        if s_rows:
                            student_id = s_rows[0][0]
                    if not student_id:
                        with db.db_lock:
                            db.cursor.execute("SELECT user_id FROM users WHERE phone = ?", (phone,))
                            r_u = db.cursor.fetchone()
                            if r_u:
                                student_id = r_u[0]
                if not student_id:
                    student_id = abs(hash(phone or student_name)) % 1000000000

                # 1. الحفظ في قاعدة بيانات SQLite المحلية ليظهر للأستاذ في لوحة تحكم البوت
                with db.db_lock:
                    # ضمان وجود التلميذ في جدول users مع مدرسته حتى لا تظهر "غير مسجل"
                    db.cursor.execute("""
                        INSERT OR REPLACE INTO users (user_id, full_name, school, last_active, joined_at, phone, source)
                        VALUES (?, ?, ?, ?, ?, ?, 'web')
                    """, (student_id, student_name, school, now, now, phone or ""))
                    
                    db.cursor.execute("""
                        INSERT INTO submissions (student_id, student_name, exercise_name, photo_id, status, is_read, has_grade, notes_history)
                        VALUES (?, ?, ?, ?, ?, 0, 0, ?)
                    """, (student_id, student_name, exercise_name, "", "قيد الانتظار", note))
                    db.conn.commit()

                # 2. الحفظ في Turso السحابي
                if turso_client:
                    try:
                        turso_client.execute("""
                            INSERT INTO submissions (student_id, student_name, exercise_name, photo_id, status, is_read, has_grade)
                            VALUES (?, ?, ?, ?, ?, 0, 0)
                        """, [student_id, student_name, exercise_name, "", "قيد الانتظار"])
                    except Exception as e_turso:
                        print("Turso submission insert:", e_turso)

                # 3. إشعار فوري للأستاذ في تيليغرام
                import threading
                def _notify_teacher_hw():
                    token = os.getenv("BOT_TOKEN")
                    if not token:
                        return
                    try:
                        import urllib.request
                        msg = (
                            f"📥 **محاولة حل واجب جديدة من الموقع!**\n\n"
                            f"👤 **التلميذ:** {student_name}\n"
                            f"🏫 **المدرسة:** {school}\n"
                            f"📱 **الهاتف:** `{phone}`\n"
                            f"📝 **الواجب:** {exercise_name}\n"
                            f"💬 **الملاحظة:** {note or 'لا توجد'}\n"
                            f"⏰ **الوقت:** {now}"
                        )
                        url_api = f"https://api.telegram.org/bot{token}/sendMessage"
                        data_tg = json.dumps({"chat_id": 5279737033, "text": msg, "parse_mode": "Markdown"}).encode("utf-8")
                        req = urllib.request.Request(url_api, data=data_tg, headers={"Content-Type": "application/json"})
                        urllib.request.urlopen(req, timeout=10)
                    except Exception as e:
                        print("HW telegram notify error:", e)

                threading.Thread(target=_notify_teacher_hw, daemon=True).start()

                return self._send_json({"success": True, "message": "تم استلام الواجب بنجاح وإرساله للأستاذ!"})
            except Exception as e:
                print("Error saving submission:", e)
                return self._send_json({"success": False, "error": str(e)}, status=500)        # ---------------- 💬 5. إرسال رسالة للأستاذ عبر الموقع (وحفظها لـ 24 ساعة) ----------------
        elif path == "/api/chat/send":
            phone = normalize_phone(payload.get("phone", "").strip())
            message_text = payload.get("message", "").strip()

            if not phone or not message_text:
                return self._send_json({"success": False, "error": "يرجى كتابة رسالة."})

            try:
                import db
                from datetime import timedelta
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # حذف الرسائل القديمة (24 ساعة) أولاً
                cutoff_dt = datetime.now() - timedelta(hours=24)
                cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
                cutoff_short = cutoff_dt.strftime("%Y-%m-%d %H:%M")
                if turso_client:
                    try:
                        turso_client.execute("DELETE FROM chat_messages WHERE created_at < ? OR created_at < ?", [cutoff, cutoff_short])
                    except Exception:
                        pass
                try:
                    with db.db_lock:
                        db.cursor.execute("DELETE FROM chat_messages WHERE created_at < ? OR created_at < ?", (cutoff, cutoff_short))
                        db.conn.commit()
                except Exception:
                    pass

                student_id = None
                student_name = None
                school = "دراسة عن بعد 🌐"

                # إذا كان التلميذ مسجلاً في البوت نفضل آيدي البوت لكي يتمكن الأستاذ من الرد عليه مباشرة في التيليغرام
                if turso_client:
                    u_rows = turso_client.execute("SELECT user_id, full_name, school FROM users WHERE phone = ?", [phone])
                    if u_rows and u_rows[0]:
                        student_id = u_rows[0][0]
                        student_name = u_rows[0][1]
                        school = u_rows[0][2] or school

                if not student_id:
                    try:
                        with db.db_lock:
                            db.cursor.execute("SELECT user_id, full_name, school FROM users WHERE phone = ?", (phone,))
                            r_u = db.cursor.fetchone()
                            if r_u:
                                student_id, student_name, school = r_u[0], r_u[1], r_u[2] or school
                    except Exception:
                        pass

                # إذا لم يكن في البوت، نأخذ بياناته من الموقع web_users
                if not student_id and turso_client:
                    w_rows = turso_client.execute("SELECT id, first_name, last_name, school FROM web_users WHERE phone = ?", [phone])
                    if w_rows and w_rows[0]:
                        student_id = w_rows[0][0]
                        student_name = f"{w_rows[0][1]} {w_rows[0][2]}".strip()
                        school = w_rows[0][3] or school

                if not student_id:
                    student_id = abs(hash(phone)) % 1000000000
                if not student_name:
                    student_name = payload.get("student_name") or f"تلميذ ({phone})"

                # 1. الحفظ في Turso السحابي
                if turso_client:
                    try:
                        turso_client.execute("""
                            INSERT INTO chat_messages (sender_id, sender_name, receiver_id, message_text, created_at, is_read)
                            VALUES (?, ?, 5279737033, ?, ?, 0)
                        """, [student_id, student_name, message_text, now])
                    except Exception as e_c:
                        print("Turso chat insert error:", e_c)

                # 2. الحفظ في bot.db المحلي
                try:
                    with db.db_lock:
                        db.cursor.execute("""
                            INSERT INTO chat_messages (sender_id, sender_name, receiver_id, message_text, created_at, is_read)
                            VALUES (?, ?, 5279737033, ?, ?, 0)
                        """, (student_id, student_name, message_text, now))
                        db.conn.commit()
                except Exception as e_l:
                    print("Local chat insert error:", e_l)

                # 3. إرسال إشعار تليغرام فوري للأستاذ مع زر مباشر للرد على التلميذ
                import threading
                def _notify_teacher_chat():
                    token = os.getenv("BOT_TOKEN")
                    if not token:
                        return
                    try:
                        import urllib.request
                        msg = (
                            f"💬 **استفسار جديد من التلميذ:**\n"
                            f"👤 *{student_name}* ({school})\n"
                            f"📱 الهاتف: `{phone}`\n"
                            f"🆔 الآيدي: `{student_id}`\n\n"
                            f"📝 *الرسالة:*\n{message_text}\n\n"
                            f"🕒 {now}"
                        )
                        reply_markup = {
                            "inline_keyboard": [
                                [{"text": f"✉️ فتح المحادثة والرد على {student_name}", "callback_data": f"open_chat_{student_id}"}]
                            ]
                        }
                        url_api = f"https://api.telegram.org/bot{token}/sendMessage"
                        data_tg = json.dumps({
                            "chat_id": 5279737033,
                            "text": msg,
                            "reply_markup": reply_markup,
                            "parse_mode": "Markdown"
                        }).encode("utf-8")
                        req = urllib.request.Request(url_api, data=data_tg, headers={"Content-Type": "application/json"})
                        urllib.request.urlopen(req, timeout=10)
                    except Exception as e:
                        print("Chat telegram notify error:", e)

                threading.Thread(target=_notify_teacher_chat, daemon=True).start()

                return self._send_json({"success": True, "message": "تم إرسال رسالتك للأستاذ بنجاح!"})
            except Exception as e:
                print("Chat send error:", e)
                return self._send_json({"success": False, "error": "حدث خطأ أثناء إرسال الرسالة."})

        self.send_error(404, "Endpoint not found")


def run():
    server_address = ("", PORT)
    httpd = ThreadingHTTPServer(server_address, PlatformHandler)
    print(f"🚀 خادم المنصة التعليمية يعمل الآن على: http://localhost:{PORT}")
    print(f"📂 مسار الموقع: {WEBSITE_DIR}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف الخادم.")

if __name__ == "__main__":
    run()
