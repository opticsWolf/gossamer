@echo off
setlocal
set "ROOT=%~dp0.."
if defined GOSSAMER_RUST_BIN (
  "%GOSSAMER_RUST_BIN%" %*
  exit /b %ERRORLEVEL%
)
for %%P in (release debug) do (
  if exist "%ROOT%\rust\app\target\%%P\gossamer-mcp.exe" (
    "%ROOT%\rust\app\target\%%P\gossamer-mcp.exe" %*
    exit /b %ERRORLEVEL%
  )
)
echo gossamer-mcp Rust binary not found (set GOSSAMER_RUST_BIN) 1>&2
exit /b 1
