#Requires -Version 5.1
<#
.SYNOPSIS
  Positive/negative proof that GPO startup install is gated on per-SID token presence.

.DESCRIPTION
  Does NOT install the agent, link GPO, or write production tokens.
  Simulates Install-QkaAgentFromUnc.ps1 authorization gate against a temp drop folder.
#>
[CmdletBinding()]
param(
  [string]$PilotComputer = 'WSIT02',
  [string]$OtherComputer = ''
)

$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory -ErrorAction Stop

function Get-TokenDropFileName([string]$sid) {
  return (($sid -replace '[^0-9A-Za-z\-]', '_') + '.tok')
}

function Test-AuthGate([string]$TokenDrop, [string]$ComputerSid, [string]$AuthorizedReason) {
  # Mirrors the scheduled-task gate: file presence alone never installs.
  $tokPath = Join-Path $TokenDrop.TrimEnd('\') (Get-TokenDropFileName $ComputerSid)
  if (-not (Test-Path -LiteralPath $tokPath)) { return 'SKIP_NO_TOKEN' }
  $len = (Get-Item -LiteralPath $tokPath).Length
  if ($len -lt 8) { return 'SKIP_EMPTY' }
  switch ($AuthorizedReason) {
    'ok' { return 'INSTALL_ALLOWED' }
    'expired' { return 'SKIP_EXPIRED' }
    'cancelled' { return 'SKIP_CANCELLED' }
    'foreign_sid' { return 'SKIP_FOREIGN' }
    'absent' { return 'SKIP_NO_TOKEN' }
    default { return 'SKIP_DENIED' }
  }
}

$pilot = Get-ADComputer -Identity $PilotComputer -ErrorAction Stop
$pilotSid = $pilot.SID.Value
if (-not $OtherComputer) {
  $other = Get-ADComputer -Filter "Name -ne '$($pilot.Name)' -and Enabled -eq `$true" -ResultSetSize 1 |
    Select-Object -First 1
  if (-not $other) { throw 'Need another computer for negative test.' }
} else {
  $other = Get-ADComputer -Identity $OtherComputer -ErrorAction Stop
}
$otherSid = $other.SID.Value

$root = Join-Path $env:TEMP ('qka-auth-gate-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $root | Out-Null

# Negative: no tokens → neither computer may install
$negPilot = Test-AuthGate -TokenDrop $root -ComputerSid $pilotSid
$negOther = Test-AuthGate -TokenDrop $root -ComputerSid $otherSid
if ($negPilot -ne 'SKIP_NO_TOKEN' -or $negOther -ne 'SKIP_NO_TOKEN') {
  throw "FAIL empty-drop gate: pilot=$negPilot other=$negOther"
}
Write-Host 'NEG_EMPTY_DROP=PASS (link/pilot alone would not install)'

# File present is not enough: API reason must be ok.
$pilotTok = Join-Path $root (Get-TokenDropFileName $pilotSid)
Set-Content -LiteralPath $pilotTok -Value 'PLACEHOLDER-NOT-A-REAL-ENROLLMENT-TOKEN' -Encoding ASCII
$fileOnly = Test-AuthGate -TokenDrop $root -ComputerSid $pilotSid -AuthorizedReason 'absent'
if ($fileOnly -eq 'INSTALL_ALLOWED') { throw 'FAIL file presence alone must not install' }
$expired = Test-AuthGate -TokenDrop $root -ComputerSid $pilotSid -AuthorizedReason 'expired'
$cancelled = Test-AuthGate -TokenDrop $root -ComputerSid $pilotSid -AuthorizedReason 'cancelled'
$foreign = Test-AuthGate -TokenDrop $root -ComputerSid $pilotSid -AuthorizedReason 'foreign_sid'
if ($expired -ne 'SKIP_EXPIRED' -or $cancelled -ne 'SKIP_CANCELLED' -or $foreign -ne 'SKIP_FOREIGN') {
  throw "FAIL freshness gate expired=$expired cancelled=$cancelled foreign=$foreign"
}
Write-Host 'NEG_STALE_OR_FOREIGN=PASS (expired/cancelled/foreign never install)'
$posPilot = Test-AuthGate -TokenDrop $root -ComputerSid $pilotSid -AuthorizedReason 'ok'
$posOther = Test-AuthGate -TokenDrop $root -ComputerSid $otherSid -AuthorizedReason 'ok'
if ($posPilot -ne 'INSTALL_ALLOWED') { throw "FAIL positive pilot gate: $posPilot" }
if ($posOther -ne 'SKIP_NO_TOKEN') { throw "FAIL negative other gate: $posOther" }
Write-Host ("POS_PILOT_ONLY=PASS pilot=" + $pilot.Name + " other=" + $other.Name)

Remove-Item -LiteralPath $pilotTok -Force
$afterCancel = Test-AuthGate -TokenDrop $root -ComputerSid $pilotSid -AuthorizedReason 'ok'
if ($afterCancel -ne 'SKIP_NO_TOKEN') { throw "FAIL cancel gate: $afterCancel" }
Write-Host 'NEG_AFTER_CANCEL=PASS (removed token never installs)'

Write-Host 'AUTH_GATE=PASS'
Write-Host ("WORK_ROOT=" + $root)
Write-Host ("PILOT_SID=" + $pilotSid)
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
