# Wrapper: build the firmware in place with the locally installed ESP-IDF v6.0.
# Kept as a small script so the environment setup (PATH/IDF_* vars, MSYSTEM
# removal for Git Bash launched sessions) stays in one place.
$ErrorActionPreference = "Stop"

$espressifRoot = "D:\Espressif"
$idfPath = Join-Path $espressifRoot "frameworks\esp-idf-v6.0"
$idfPython = Join-Path $espressifRoot "python_env\idf6.0_py3.14_env\Scripts\python.exe"
$cmakeBin = Join-Path $espressifRoot "tools\cmake\4.0.3\bin"
$ninjaBin = Join-Path $espressifRoot "tools\ninja\1.12.1"
$toolchainBin = Join-Path $espressifRoot "tools\xtensa-esp-elf\esp-15.2.0_20251204\xtensa-esp-elf\bin"

Remove-Item Env:MSYSTEM -ErrorAction SilentlyContinue

$env:IDF_PATH = $idfPath
$env:IDF_TOOLS_PATH = $espressifRoot
$env:IDF_PYTHON_ENV_PATH = Join-Path $espressifRoot "python_env\idf6.0_py3.14_env"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
# idf_component_manager crashes if this is unset (Version.coerce(None))
$env:ESP_IDF_VERSION = "6.0"
$env:PATH = "$cmakeBin;$ninjaBin;$toolchainBin;$env:PATH"

$idfPy = Join-Path $idfPath "tools\idf.py"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectDir
try {
    & $idfPython $idfPy @args
    if ($LASTEXITCODE -ne 0) { throw "idf.py failed: $LASTEXITCODE" }
}
finally {
    Pop-Location
}
