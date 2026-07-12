# Sync Windows host ARP table into DitakNet data folder for Docker discovery.
# Run on the HOST (PowerShell), not inside the container.
# Schedule every 5 minutes for best MAC/vendor visibility on Docker Desktop.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "data\host_arp.json"

$map = @{}
arp -a | ForEach-Object {
    if ($_ -match '(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F\-]{17})\s+dynamic') {
        $ip = $matches[1]
        $mac = ($matches[2] -replace '-', ':').ToUpper()
        if ($mac -ne 'FF:FF:FF:FF:FF:FF') {
            $map[$ip] = $mac
        }
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
$json = $map | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($out, $json, (New-Object System.Text.UTF8Encoding $false))
Write-Host "Wrote $($map.Count) ARP entries to $out"
