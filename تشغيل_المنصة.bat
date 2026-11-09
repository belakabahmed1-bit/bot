@echo off
chcp 65001 > nul
title منصة الأستاذ بلعقاب للرياضيات
echo =======================================================
echo    جاري تشغيل منصة الأستاذ بلعقاب للرياضيات...
echo    الخادم يعمل على: http://localhost:5000
echo =======================================================
echo.
start "" "http://localhost:5000/register.html"
python web_server.py
pause
