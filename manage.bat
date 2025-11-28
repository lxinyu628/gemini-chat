@echo off
REM Business Gemini 服务管理脚本 (Windows)
REM 支持: start, stop, restart, status, reload, logs

setlocal enabledelayedexpansion

REM 项目目录
set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "CONFIG_FILE=%PROJECT_DIR%\config.json"
set "LOG_DIR=%PROJECT_DIR%\log"
set "PID_FILE=%LOG_DIR%\server.pid"
set "ACCESS_LOG=%LOG_DIR%\access.log"
set "ERROR_LOG=%LOG_DIR%\error.log"
set "VENV_DIR=%PROJECT_DIR%\venv"

REM 确保日志目录存在
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM 解析命令
set "CMD=%~1"
if "%CMD%"=="" goto :usage

if /i "%CMD%"=="start" goto :start
if /i "%CMD%"=="stop" goto :stop
if /i "%CMD%"=="restart" goto :restart
if /i "%CMD%"=="status" goto :status
if /i "%CMD%"=="reload" goto :reload
if /i "%CMD%"=="logs" goto :logs
goto :usage

:start
echo [*] 启动 Business Gemini 服务...

REM 检查是否已在运行
if exist "%PID_FILE%" (
    set /p PID=<"%PID_FILE%"
    tasklist /FI "PID eq !PID!" 2>NUL | find /I /N "python">NUL
    if !ERRORLEVEL! equ 0 (
        echo [!] 服务已在运行 (PID: !PID!)
        exit /b 1
    ) else (
        echo [!] 发现旧的 PID 文件，清理中...
        del /f /q "%PID_FILE%" 2>NUL
    )
)

REM 激活虚拟环境
if exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [*] 激活虚拟环境...
    call "%VENV_DIR%\Scripts\activate.bat"
) else (
    echo [!] 未找到虚拟环境，使用系统 Python
)

REM 读取配置
set "BIND_HOST=0.0.0.0"
set "BIND_PORT=8000"
set "WORKERS=4"
set "LOG_LEVEL=INFO"

if exist "%CONFIG_FILE%" (
    for /f "tokens=*" %%i in ('python -c "import json; cfg=json.load(open('%CONFIG_FILE%', encoding='utf-8')); srv=cfg.get('server', {}); print(f\"{srv.get('host', '0.0.0.0')}^|{srv.get('port', 8000)}^|{srv.get('workers', 4)}^|{srv.get('log_level', 'INFO')}\")" 2^>NUL') do (
        for /f "tokens=1,2,3,4 delims=|" %%a in ("%%i") do (
            set "BIND_HOST=%%a"
            set "BIND_PORT=%%b"
            set "WORKERS=%%c"
            set "LOG_LEVEL=%%d"
        )
    )
)

REM 环境变量优先
if defined SERVER_HOST set "BIND_HOST=%SERVER_HOST%"
if defined SERVER_PORT set "BIND_PORT=%SERVER_PORT%"
if defined SERVER_WORKERS set "WORKERS=%SERVER_WORKERS%"
if defined SERVER_LOG_LEVEL set "LOG_LEVEL=%SERVER_LOG_LEVEL%"

echo [*] 绑定地址: %BIND_HOST%:%BIND_PORT%
echo [*] Worker 数量: %WORKERS%
echo [*] 日志级别: %LOG_LEVEL%

REM 检查依赖
python -c "import uvicorn, fastapi" 2>NUL
if errorlevel 1 (
    echo [!] 缺少必要依赖，请运行: pip install -r requirements.txt
    exit /b 1
)

REM 切换到项目目录
cd /d "%PROJECT_DIR%"

REM 启动服务（后台运行）
echo [*] 启动服务...
start /b python -m uvicorn server:app --host %BIND_HOST% --port %BIND_PORT% --log-level %LOG_LEVEL% > "%ACCESS_LOG%" 2> "%ERROR_LOG%"

REM 获取进程 ID
timeout /t 2 /nobreak >NUL
for /f "tokens=2" %%i in ('tasklist /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq *uvicorn*" /NH 2^>NUL ^| find "python.exe"') do (
    echo %%i > "%PID_FILE%"
    set "PID=%%i"
    goto :start_success
)

REM 如果没找到进程，尝试备用方法
powershell -Command "$proc = Get-Process python -ErrorAction SilentlyContinue | Where-Object {$_.CommandLine -like '*uvicorn*'} | Select-Object -First 1; if ($proc) { $proc.Id | Out-File -FilePath '%PID_FILE%' -Encoding ascii }"
if exist "%PID_FILE%" (
    set /p PID=<"%PID_FILE%"
    goto :start_success
)

echo [!] 服务启动失败，请查看日志: %ERROR_LOG%
exit /b 1

:start_success
echo [+] 服务启动成功 (PID: %PID%)
echo [+] 访问地址: http://%BIND_HOST%:%BIND_PORT%
exit /b 0

:stop
echo [*] 停止 Business Gemini 服务...

if not exist "%PID_FILE%" (
    echo [!] 服务未运行（无 PID 文件）
    exit /b 1
)

set /p PID=<"%PID_FILE%"
tasklist /FI "PID eq %PID%" 2>NUL | find /I /N "python">NUL
if errorlevel 1 (
    echo [!] 进程不存在 (PID: %PID%)
    del /f /q "%PID_FILE%" 2>NUL
    exit /b 1
)

echo [*] 正在停止进程 (PID: %PID%)...
taskkill /PID %PID% /F >NUL 2>&1
del /f /q "%PID_FILE%" 2>NUL
echo [+] 服务已停止
exit /b 0

:restart
echo [*] 重启服务...
call :stop
timeout /t 2 /nobreak >NUL
call :start
exit /b %ERRORLEVEL%

:status
if not exist "%PID_FILE%" (
    echo [!] 服务未运行
    exit /b 1
)

set /p PID=<"%PID_FILE%"
tasklist /FI "PID eq %PID%" 2>NUL | find /I /N "python">NUL
if errorlevel 1 (
    echo [!] 服务未运行（PID 文件存在但进程不存在）
    exit /b 1
)

REM 读取配置获取地址
set "BIND_HOST=0.0.0.0"
set "BIND_PORT=8000"
if exist "%CONFIG_FILE%" (
    for /f "tokens=*" %%i in ('python -c "import json; cfg=json.load(open('%CONFIG_FILE%', encoding='utf-8')); srv=cfg.get('server', {}); print(f\"{srv.get('host', '0.0.0.0')}^|{srv.get('port', 8000)}\")" 2^>NUL') do (
        for /f "tokens=1,2 delims=|" %%a in ("%%i") do (
            set "BIND_HOST=%%a"
            set "BIND_PORT=%%b"
        )
    )
)

echo [+] 服务运行中
echo     PID: %PID%
echo     地址: http://%BIND_HOST%:%BIND_PORT%
exit /b 0

:reload
echo [*] 重载配置...
echo [!] Windows 不支持热重载，请使用 restart 命令重启服务
exit /b 1

:logs
set "LOG_TYPE=%~2"
if "%LOG_TYPE%"=="" set "LOG_TYPE=error"

if /i "%LOG_TYPE%"=="access" (
    set "LOG_FILE=%ACCESS_LOG%"
) else (
    set "LOG_FILE=%ERROR_LOG%"
)

if not exist "%LOG_FILE%" (
    echo [!] 日志文件不存在: %LOG_FILE%
    echo [*] 可用的日志文件:
    dir /b "%LOG_DIR%\*.log" 2>NUL
    exit /b 1
)

echo [*] 显示日志: %LOG_FILE%
echo [*] 按 Ctrl+C 退出
powershell -Command "Get-Content '%LOG_FILE%' -Wait -Tail 50"
exit /b 0

:usage
echo 用法: %~nx0 {start^|stop^|restart^|status^|reload^|logs [access^|error]}
echo.
echo 命令说明:
echo   start   - 启动服务
echo   stop    - 停止服务
echo   restart - 重启服务
echo   status  - 查看服务状态
echo   reload  - 重载配置（Windows 不支持，请使用 restart）
echo   logs    - 查看日志 (默认 error，可选 access/error)
echo.
echo 环境变量:
echo   SERVER_HOST      - 绑定地址 (默认: 0.0.0.0)
echo   SERVER_PORT      - 绑定端口 (默认: 8000)
echo   SERVER_WORKERS   - Worker 数量 (默认: 4)
echo   SERVER_LOG_LEVEL - 日志级别 (默认: INFO)
exit /b 1
