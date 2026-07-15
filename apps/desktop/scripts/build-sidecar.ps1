param(
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$DesktopRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SpecPath = Join-Path $DesktopRoot "backend\echospeak_backend.spec"
$TauriRoot = Join-Path $DesktopRoot "src-tauri"
$BuildRoot = Join-Path $DesktopRoot ".build\sidecar"
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$BinaryRoot = Join-Path $TauriRoot "binaries"

if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
    $CargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
    if (Test-Path -LiteralPath (Join-Path $CargoBin "rustc.exe")) {
        $env:Path = "$CargoBin;$env:Path"
    }
}
if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
    throw "Rust 1.84.0 or newer is required so the sidecar target triple can be determined. Restart the shell if Rust was just installed."
}
$PyInstallerVersion = (& $PythonExecutable -m PyInstaller --version).Trim()
if ($LASTEXITCODE -ne 0 -or $PyInstallerVersion -ne "6.21.0") {
    throw "PyInstaller 6.21.0 is required; found '$PyInstallerVersion'."
}
$TargetTriple = (& rustc --print host-tuple).Trim()
if (-not $TargetTriple) {
    throw "rustc did not return a host target triple."
}

New-Item -ItemType Directory -Force -Path $DistRoot, $WorkRoot, $BinaryRoot | Out-Null
& $PythonExecutable -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $DistRoot `
    --workpath $WorkRoot `
    $SpecPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

$SourceBinary = Join-Path $DistRoot "echospeak-backend.exe"
if (-not (Test-Path -LiteralPath $SourceBinary)) {
    throw "Expected sidecar was not produced at $SourceBinary."
}
$TargetBinary = Join-Path $BinaryRoot "echospeak-backend-$TargetTriple.exe"
Copy-Item -LiteralPath $SourceBinary -Destination $TargetBinary -Force
Write-Host "Sidecar ready: $TargetBinary"
