"""نسخ ملفات وصور البوت (المخزنة في تيليغرام) إلى Turso ليعرضها الموقع دون تيليغرام.

يعمل في الخلفية: كل 5 دقائق يبحث عن أي file_id مستعمل في الدروس والتمارين والواجبات والحوليات
والملفات ولم يُنسخ بعد، فينزّله ويحفظه في جدولي media_files و media_chunks.
لا يغيّر شيئًا في ملفات البوت: الأرقام (file_id) تبقى في أماكنها كما هي.
"""
import base64
import json
import mimetypes
import threading
import time
from datetime import datetime

import db

CHUNK = 128 * 1024              # حجم القطعة الخام قبل التحويل إلى نص
MAX_BYTES = 20 * 1024 * 1024    # أقصى حجم يسمح تيليغرام للبوت بتنزيله
INTERVAL = 300                  # ثواني بين كل فحص
_fails = {}


def _q(sql, args=(), commit=False):
    with db.db_lock:
        db.cursor.execute(sql, args)
        if commit:
            db.conn.commit()
            return None
        return db.cursor.fetchall()


def _init():
    _q("CREATE TABLE IF NOT EXISTS media_files (file_id TEXT PRIMARY KEY, mime TEXT, size INTEGER, created_at TEXT)", commit=True)
    _q("CREATE TABLE IF NOT EXISTS media_chunks (file_id TEXT, seq INTEGER, data TEXT, PRIMARY KEY (file_id, seq))", commit=True)


def referenced_ids():
    """كل file_id مستعمل حاليًا في قاعدة البيانات."""
    ids = set()
    for pdf, sol, extra in _q("SELECT pdf_file_id, solution_file_id, extra_media FROM exercises"):
        ids.update(x for x in (pdf, sol) if x)
        try:
            ids.update(m["file_id"] for m in json.loads(extra or "[]") if isinstance(m, dict) and m.get("file_id"))
        except Exception:
            pass
    ids.update(r[0] for r in _q("SELECT pdf_file_id FROM bac_topics") if r[0])
    ids.update(r[0] for r in _q("SELECT file_id FROM custom_files") if r[0])
    return ids


def _sniff(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    return None


def mirror_one(bot, fid):
    info = bot.get_file(fid)
    if info.file_size and info.file_size > MAX_BYTES:
        raise ValueError("الملف أكبر من 20MB (حد تيليغرام)")
    data = bot.download_file(info.file_path)
    mime = _sniff(data) or mimetypes.guess_type(info.file_path)[0] or "application/octet-stream"
    _q("DELETE FROM media_chunks WHERE file_id=?", (fid,), commit=True)
    for i in range(0, len(data), CHUNK):
        _q("INSERT INTO media_chunks (file_id, seq, data) VALUES (?,?,?)",
           (fid, i // CHUNK, base64.b64encode(data[i:i + CHUNK]).decode()), commit=True)
    # السجل الرئيسي يُكتب أخيرًا: لا يظهر ملف ناقص أبدًا
    _q("INSERT OR REPLACE INTO media_files (file_id, mime, size, created_at) VALUES (?,?,?,?)",
       (fid, mime, len(data), datetime.now().strftime("%Y-%m-%d %H:%M")), commit=True)


def sync_once(bot):
    done = {r[0] for r in _q("SELECT file_id FROM media_files")}
    todo = [f for f in referenced_ids() - done if _fails.get(f, 0) < 3]
    for fid in todo:
        try:
            mirror_one(bot, fid)
            print(f"media mirrored: {fid[:12]}…")
        except Exception as e:
            _fails[fid] = _fails.get(fid, 0) + 1
            print(f"media mirror failed ({_fails[fid]}/3): {fid[:12]}… {repr(e)[:120]}")
        time.sleep(0.5)
    return len(todo)


def start_file_sync(bot):
    def loop():
        try:
            _init()
        except Exception as e:
            print("media sync init error:", repr(e))
            return
        while True:
            try:
                sync_once(bot)
            except Exception as e:
                print("media sync error:", repr(e))
            time.sleep(INTERVAL)

    threading.Thread(target=loop, daemon=True, name="media-sync").start()
