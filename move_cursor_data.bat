@echo off
chcp 65001 >nul
echo ================================================================
echo   将 Cursor 数据从 C 盘迁移到 D 盘
echo ================================================================
echo.

:: 检查 Cursor 是否在运行
tasklist /FI "IMAGENAME eq Cursor.exe" 2>NUL | find /I "Cursor.exe" >NUL
if %ERRORLEVEL%==0 (
    echo [ERROR] Cursor 正在运行，请先关闭 Cursor 再执行此脚本！
    echo.
    pause
    exit /b 1
)

set SOURCE=C:\Users\pluto4u\.cursor
set TARGET=D:\cursor-data\.cursor

:: 检查源目录
if not exist "%SOURCE%" (
    echo [ERROR] 源目录不存在: %SOURCE%
    pause
    exit /b 1
)

:: 检查是否已经是 junction
fsutil reparsepoint query "%SOURCE%" >nul 2>&1
if %ERRORLEVEL%==0 (
    echo [INFO] %SOURCE% 已经是一个链接，无需再次迁移。
    pause
    exit /b 0
)

echo [1/3] 创建目标目录...
if not exist "D:\cursor-data" mkdir "D:\cursor-data"

echo [2/3] 移动数据到 D 盘 (可能需要几分钟)...
echo       %SOURCE%  -^>  %TARGET%
robocopy "%SOURCE%" "%TARGET%" /E /MOVE /R:2 /W:1 /NFL /NDL /NJH /NJS

if exist "%SOURCE%" (
    echo [WARN] 源目录未完全清空，尝试强制删除残留...
    rmdir /S /Q "%SOURCE%" 2>nul
)

if exist "%SOURCE%" (
    echo [ERROR] 无法删除源目录，可能有文件被占用。
    echo         请确认 Cursor 和所有 VS Code 进程已关闭后重试。
    pause
    exit /b 1
)

echo [3/3] 创建 Junction 链接...
mklink /J "%SOURCE%" "%TARGET%"

if %ERRORLEVEL%==0 (
    echo.
    echo ================================================================
    echo   迁移完成！
    echo   物理位置: %TARGET%
    echo   链接位置: %SOURCE% -^> %TARGET%
    echo   现在可以重新打开 Cursor 了。
    echo ================================================================
) else (
    echo [ERROR] 创建链接失败，请以管理员身份运行此脚本。
    echo         数据已移动到: %TARGET%
    echo         需要手动执行: mklink /J "%SOURCE%" "%TARGET%"
)

echo.
pause
