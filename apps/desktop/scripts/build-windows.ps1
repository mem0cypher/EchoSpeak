param(
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$DesktopRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $DesktopRoot "..\..")).Path
$WebRoot = Join-Path $RepoRoot "apps\web"

& (Join-Path $PSScriptRoot "build-sidecar.ps1") -PythonExecutable $PythonExecutable
if ($LASTEXITCODE -ne 0) {
    throw "Sidecar preparation failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path -LiteralPath (Join-Path $WebRoot "node_modules"))) {
    npm --prefix $WebRoot ci
    if ($LASTEXITCODE -ne 0) { throw "Web dependency install failed." }
}
if (-not (Test-Path -LiteralPath (Join-Path $DesktopRoot "node_modules"))) {
    npm --prefix $DesktopRoot ci
    if ($LASTEXITCODE -ne 0) { throw "Desktop dependency install failed." }
}

npm --prefix $DesktopRoot run tauri build
if ($LASTEXITCODE -ne 0) {
    throw "Tauri Windows build failed with exit code $LASTEXITCODE."
}

$BundleRoot = Join-Path $DesktopRoot "src-tauri\target\release\bundle"
Write-Host "Windows bundles: $BundleRoot"
