@echo off
chcp 65001 >nul
echo ========================================
echo 语音通话修复 - 客户端重启脚本
echo ========================================
echo.

echo [1/4] 关闭所有客户端进程...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *tk_main*" 2>nul
timeout /t 2 /nobreak >nul

echo [2/4] 清理Python缓存...
for /r %%i in (*.pyc) do @del "%%i" 2>nul
for /d /r %%i in (__pycache__) do @rd /s /q "%%i" 2>nul

echo [3/4] 验证修复状态...
python verify_fix.py

echo.
echo [4/4] 准备启动客户端...
echo.
echo 请在两个新的命令提示符窗口中分别运行:
echo.
echo   窗口1 (alice):  python -m client.tk_main
echo   窗口2 (bob):    python -m client.tk_main
echo.
echo ========================================
echo 提示: 如果服务器没运行，先运行 python -m server.main
echo ========================================
pause
