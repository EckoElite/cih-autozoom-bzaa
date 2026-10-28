<#
Install or remove the prototype add-on service.cih.zoom on the Shield over adb.

  .\install_addon.ps1 -Install     copy the add-on into Kodi's addons folder (adb push), then restart Kodi
  .\install_addon.ps1 -Uninstall   remove it again (adb shell rm -r), then restart Kodi
  .\install_addon.ps1 -Pull        copy the add-on's events.jsonl log back to tools\testpatterns\out

Reads SHIELD_IP / SHIELD_ADB_PORT from ..\..\.env. Nothing is run until you pass one of the switches.
The add-on only acts on playback of URLs matching /boundary_*.mkv; everything else is left alone.
#>
param([switch]$Install, [switch]$Uninstall, [switch]$Pull)

$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$env0 = @{}
Get-Content (Join-Path $repo '.env') | ForEach-Object { if ($_ -match '^\s*([A-Z_]+)=(.*)$') { $env0[$matches[1]] = $matches[2].Trim() } }
$dev = "$($env0.SHIELD_IP):$($env0.SHIELD_ADB_PORT)"
$kodi = '/sdcard/Android/data/org.xbmc.kodi/files/.kodi'
$src = Join-Path $repo 'players\kodi\service.cih.zoom'

function Restart-Kodi {
    adb -s $dev shell am force-stop org.xbmc.kodi
    Start-Sleep 2
    adb -s $dev shell monkey -p org.xbmc.kodi -c android.intent.category.LAUNCHER 1 | Out-Null
    Write-Host 'Kodi restarted; give it about 20 seconds to load.'
}

adb connect $dev | Out-Null
if ($Install) {
    Get-ChildItem $src -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    adb -s $dev push $src "$kodi/addons/"
    Restart-Kodi
    Write-Host 'Next: enable the add-on (Addons.SetAddonEnabled service.cih.zoom) if Kodi asks or leaves it disabled.'
}
elseif ($Uninstall) {
    adb -s $dev shell rm -r "$kodi/addons/service.cih.zoom" "$kodi/userdata/addon_data/service.cih.zoom"
    Restart-Kodi
}
elseif ($Pull) {
    $out = Join-Path $repo 'tools\testpatterns\out'
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    adb -s $dev pull "$kodi/userdata/addon_data/service.cih.zoom/events.jsonl" (Join-Path $out "addon_events_$stamp.jsonl")
}
else {
    Write-Host 'Pass -Install, -Uninstall or -Pull.'
}
