@echo off
setlocal
REM Copy bundled FFmpeg binaries to tools\ffmpeg.exe (MATRIX-NEO default layout).
REM Source priority: tools\ffmpeg-8.1.1\bin > tools\ffmpeg-*-essentials_build\bin

set "ROOT=%~dp0"
set "SRC="

if exist "%ROOT%ffmpeg-8.1.1\bin\ffmpeg.exe" (
    set "SRC=%ROOT%ffmpeg-8.1.1\bin"
    goto :copy
)

for /f "delims=" %%D in ('dir /b /ad /o-n "%ROOT%ffmpeg-*-essentials_build" 2^>nul') do (
    if exist "%ROOT%%%~D\bin\ffmpeg.exe" (
        set "SRC=%ROOT%%%~D\bin"
        goto :copy
    )
)

echo [sync-ffmpeg] No bundled ffmpeg bin folder found.
echo Place ffmpeg-8.1.1-full_build or essentials_build under tools\ and re-run.
exit /b 1

:copy
echo [sync-ffmpeg] From: %SRC%
copy /Y "%SRC%\ffmpeg.exe" "%ROOT%ffmpeg.exe" >nul
if exist "%SRC%\ffprobe.exe" copy /Y "%SRC%\ffprobe.exe" "%ROOT%ffprobe.exe" >nul
"%ROOT%ffmpeg.exe" -version | findstr /i "ffmpeg version"
echo [sync-ffmpeg] OK: tools\ffmpeg.exe
exit /b 0
