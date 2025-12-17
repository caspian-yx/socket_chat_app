@echo off
chcp 65001 >nul
echo ========================================
echo 强制清理并重启客户端
echo ========================================
echo.

echo [步骤1/5] 显示当前Python进程...
tasklist | findstr python.exe
echo.

echo [步骤2/5] 强制结束所有Python进程...
taskkill /F /IM python.exe /T 2>nul
if errorlevel 1 (
    echo 没有运行中的Python进程
) else (
    echo Python进程已终止
)
timeout /t 2 /nobreak >nul

echo.
echo [步骤3/5] 验证Python进程已清除...
tasklist | findstr python.exe
if errorlevel 1 (
    echo [OK] 所有Python进程已清除
) else (
    echo [警告] 仍有Python进程残留，请手动结束
    pause
)

echo.
echo [步骤4/5] 清理Python缓存...
del /s /q *.pyc >nul 2>&1
for /d /r %%i in (__pycache__) do @rd /s /q "%%i" 2>nul

echo.
echo [步骤5/5] 准备启动新客户端...
echo.
echo ========================================
echo 现在请按以下步骤操作:
echo ========================================
echo.
echo 1. 打开第一个命令提示符，运行服务器:
echo    python -m server.main
echo.
echo 2. 等待服务器启动完成
echo.
echo 3. 打开第二个命令提示符（用户alice）:
echo    python -m client.tk_main
echo.
echo 4. 打开第三个命令提示符（用户bob）:
echo    python -m client.tk_main
echo.
echo 5. 测试语音通话
echo.
echo ========================================
echo 重要: 确保启动的是NEW窗口，不是旧窗口！
echo ========================================
pause
