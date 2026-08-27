param(
    [string]$Port = "COM9",
    [int]$Baud = 115200,
    [int]$Seconds = 45
)
$ErrorActionPreference = "Stop"

$serial = New-Object System.IO.Ports.SerialPort($Port, $Baud, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One)
$serial.ReadTimeout = 500
$serial.DtrEnable = $false
$serial.RtsEnable = $false
$serial.Open()

# Mimic esptool hard reset: pulse RTS to reboot the chip, so we capture boot logs.
$serial.RtsEnable = $true
Start-Sleep -Milliseconds 100
$serial.RtsEnable = $false

$deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
$builder = New-Object System.Text.StringBuilder
try {
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $chunk = $serial.ReadExisting()
            if ($chunk) { [void]$builder.Append($chunk) }
        } catch [TimeoutException] {}
        Start-Sleep -Milliseconds 100
    }
} finally {
    $serial.Close()
}
Write-Output $builder.ToString()
