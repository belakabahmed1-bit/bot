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
from http.server import HTTPServer, SimpleHTTPRequestHandler
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

def notify_admin_website_registration(first_name, last_name, phone, school, branch, code, now, is_active):
    """إرسال إشعار تلغرام فوري للأستاذ عند تسجيل تلميذ جديد من الموقع الإلكتروني."""
    token = os.getenv("BOT_TOKEN")
    if not token:
        return
    try:
        import urllib.request
        full_name = f"{first_name} {last_name}".strip()
        status_text = "مفعّل تلقائياً بكود مسبق ✅" if is_active == 1 else "قيد الانتظار للمراجعة ⏳"
        msg = (
            f"🔔 **طلب تسجيل جديد!**\n\n"
            f"🏷️ **المصدر:** 🌐 الموقع الإلكتروني\n"
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
                    {"text": f"✅ قبول وتفعيل {full_name}", "callback_data": f"ok_web_phone_{phone}"},
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

# عميل موحد للاتصال بـ Turso عبر HTTP Pipeline API المباشر
class TursoHTTPClient:
    def __init__(self, db_url, auth_token):
        self.endpoint = db_url.replace("libsql://", "https://").rstrip("/") + "/v2/pipeline"
        self.headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json"
        }

    def execute(self, sql, params=None):
        import requests
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

        payload = {"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]}
        try:
            res = requests.post(self.endpoint, headers=self.headers, json=payload, timeout=12).json()
            r0 = res.get("results", [{}])[0]
            if r0.get("type") == "ok":
                result = r0.get("response", {}).get("result", {})
                rows = []
                for row in result.get("rows", []):
                    rows.append([col.get("value") for col in row])
                return rows
            else:
                print("Turso HTTP query error:", r0)
                return []
        except Exception as e:
            print("Turso HTTP connection error:", e)
            return []


turso_client = None
if TURSO_URL and TURSO_TOKEN:
    try:
        turso_client = TursoHTTPClient(TURSO_URL, TURSO_TOKEN)
        # التأكد من جدول web_users بالهيكل الدقيق
        turso_client.execute("""
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
        test_rows = turso_client.execute("SELECT count(*) FROM units")
        if test_rows:
            print(f"✅ [Turso] تم الاتصال بقاعدة بيانات Turso السحابية بنجاح! عدد الوحدات: {test_rows[0][0]}")
    except Exception as e:
        print("❌ [Turso] تعذر الاتصال بـ Turso:", e)


class PlatformHandler(SimpleHTTPRequestHandler):
    """معالج طلبات الموقع وواجهات البرمجة المرتبطة بـ Turso."""

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

        # ---------------- 1. API: قائمة الوحدات من Turso ----------------
        if path == "/api/units":
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
            if turso_client and file_id and file_id != "#":
                try:
                    meta_rows = turso_client.execute("SELECT mime FROM media_files WHERE file_id=?", [file_id])
                    mime = meta_rows[0][0] if meta_rows else "application/pdf"

                    chunk_rows = turso_client.execute("SELECT data FROM media_chunks WHERE file_id=? ORDER BY seq ASC", [file_id])
                    if chunk_rows:
                        raw_data = b"".join(base64.b64decode(c[0]) for c in chunk_rows)
                        self.send_response(200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(len(raw_data)))
                        self.send_header("Content-Disposition", f"inline; filename={file_id}.pdf")
                        self.end_headers()
                        self.wfile.write(raw_data)
                        return
                except Exception as e:
                    print(f"Error serving media {file_id} from Turso:", e)

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
            secret_input = (payload.get("password") or payload.get("code") or payload.get("secret_code") or "").strip()

            if not phone or not secret_input:
                return self._send_json({"success": False, "error": "يرجى إدخال رقم الهاتف والرمز السري."})

            if turso_client:
                rows = turso_client.execute("""
                    SELECT id, first_name, last_name, phone, branch, school, pw_hash, code, active
                    FROM web_users WHERE phone = ?
                """, [phone])

                if not rows:
                    # فحص إذا كان مسجلاً في بوت التلغرام المحلي
                    try:
                        import db
                        with db.db_lock:
                            # فحص المحذوفين
                            db.cursor.execute("SELECT user_id, full_name, phone FROM deleted_users WHERE phone = ?", (phone,))
                            if db.cursor.fetchone():
                                return self._send_json({
                                    "success": False,
                                    "is_active": -1,
                                    "error": "🚫 غير مفعل! الموقع خاص بتلاميذ الأستاذ بلعقاب أسامة فقط. أرسل رسالة للأستاذ لتفعيل الحساب."
                                })

                            # فحص الانتظار
                            db.cursor.execute("SELECT user_id, full_name, school, code, phone FROM wait WHERE phone = ?", (phone,))
                            if db.cursor.fetchone():
                                return self._send_json({
                                    "success": False,
                                    "is_active": 0,
                                    "needs_activation": True,
                                    "error": "🚫 غير مفعل! الموقع خاص بتلاميذ الأستاذ بلعقاب أسامة فقط. أرسل رسالة للأستاذ لتفعيل الحساب."
                                })

                            # فحص المفعلين في البوت
                            db.cursor.execute("SELECT user_id, full_name, school, phone, code FROM users WHERE phone = ?", (phone,))
                            u_bot = db.cursor.fetchone()
                            if u_bot:
                                code_bot = str(u_bot[4] or "").strip().upper()
                                if secret_input.upper() == code_bot or not code_bot:
                                    parts = (u_bot[1] or "").split(maxsplit=1)
                                    fn = parts[0] if parts else ""
                                    ln = parts[1] if len(parts) > 1 else ""
                                    c_val = code_bot or generate_activation_code()
                                    pw_h = hash_password(c_val)
                                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                                    turso_client.execute("""
                                        INSERT OR REPLACE INTO web_users (first_name, last_name, phone, branch, school, pw_hash, code, active, created_at)
                                        VALUES (?, ?, ?, 'شعبة عامة', ?, ?, ?, 1, ?)
                                    """, [fn, ln, phone, u_bot[2] or "دراسة عن بعد 🌐", pw_h, c_val, now])
                                    rows = turso_client.execute("SELECT id, first_name, last_name, phone, branch, school, pw_hash, code, active FROM web_users WHERE phone = ?", [phone])
                    except Exception as e_sync:
                        print("Sync bot user to web error:", e_sync)

                if not rows:
                    return self._send_json({"success": False, "error": "رقم الهاتف غير مسجل! يرجى إنشاء حساب جديد أولاً عبر الموقع أو البوت."})

                u = rows[0]
                code_db = str(u[7] or "").strip().upper()
                pw_hash_db = str(u[6] or "").strip()
                input_upper = secret_input.upper()
                input_hash = hash_password(secret_input)

                is_valid = (input_upper == code_db) or (input_hash == pw_hash_db) or (secret_input == pw_hash_db) or (hash_password(input_upper) == pw_hash_db)

                if not is_valid:
                    return self._send_json({"success": False, "error": "الرمز السري غير صحيح. يرجى التأكد من الرمز الممنوح لك في بوت التليجرام."})

                is_active = int(u[8] if u[8] is not None else 0)
                full_name = f"{u[1]} {u[2]}".strip()
                user_obj = {
                    "id": u[0],
                    "full_name": full_name,
                    "phone": u[3],
                    "branch": u[4],
                    "school": u[5],
                    "activation_code": u[7],
                    "code": u[7],
                    "is_active": is_active
                }

                # فحص الطرد أو عدم التفعيل
                if is_active <= 0:
                    return self._send_json({
                        "success": False,
                        "is_active": is_active,
                        "needs_activation": True,
                        "is_banned": (is_active == -1),
                        "error": "🚫 غير مفعل! الموقع خاص بتلاميذ الأستاذ بلعقاب أسامة فقط. أرسل رسالة للأستاذ لتفعيل الحساب."
                    })

                import urllib.parse
                cookie_str = f"math_student_session={urllib.parse.quote(json.dumps(user_obj))}; Path=/; Max-Age=31536000; SameSite=Lax"
                return self._send_json({"success": True, "is_active": 1, "message": "✅ الحساب مفعّل! مرحباً بك.", "user": user_obj}, cookie=cookie_str)

            return self._send_json({"success": False, "error": "تعذر الاتصال بقاعدة البيانات."})

        # ---------------- 🔑 3. تفعيل الحساب بكود الأستاذ في Turso ----------------
        elif path == "/api/auth/activate":
            phone = payload.get("phone", "").strip()
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

            return self._send_json({"success": False, "error": "تعذر الاتصال بقاعدة البيانات."})

        # ---------------- 4. استقبال محاولة حل الواجب في Turso ----------------
        elif path == "/api/homework/submit":
            try:
                student_name = payload.get("student_name", "تلميذ من الموقع")
                exercise_name = payload.get("exercise_name", "واجب منزلي")

                if turso_client:
                    turso_client.execute("""
                        INSERT INTO submissions (student_id, student_name, exercise_name, photo_id, status, is_read, has_grade)
                        VALUES (?, ?, ?, ?, ?, 0, 0)
                    """, [1001, student_name, exercise_name, "", "قيد الانتظار"])

                return self._send_json({"success": True, "message": "تم استلام الواجب بنجاح وإرساله للأستاذ!"})
            except Exception as e:
                print("Error saving submission to Turso:", e)
                return self._send_json({"success": False, "error": str(e)}, status=500)

        self.send_error(404, "Endpoint not found")


def run():
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, PlatformHandler)
    print(f"🚀 خادم المنصة التعليمية جاهز ويعمل على: http://localhost:{PORT}")
    print(f"📁 مسار الموقع: {WEBSITE_DIR}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nتم إيقاف الخادم.")


if __name__ == "__main__":
    run()
