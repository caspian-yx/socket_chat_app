@echo off
chcp 65001 >nul
echo ========================================
echo 重启服务器 - 语音通话修复
echo ========================================
echo.

echo [1/3] 结束服务器进程...
tasklist | findstr python.exe | findstr server
taskkill /F /FI "WINDOWTITLE eq *server.main*" 2>nul
timeout /t 1 /nobreak >nul

echo.
echo [2/3] 清理Python缓存...
del /s /q server\*.pyc >nul 2>&1
for /d /r server %%i in (__pycache__) do @rd /s /q "%%i" 2>nul

echo.
echo [3/3] 启动新的服务器...
echo.
echo ========================================
echo 正在启动服务器...
echo ========================================
python -m server.main
