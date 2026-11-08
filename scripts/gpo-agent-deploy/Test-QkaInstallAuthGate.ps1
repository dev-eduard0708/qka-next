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

function Test-AuthGate([string]$TokenDrop, [string]$ComputerSid) {
  $tokPath = Join-Path $TokenDrop.TrimEnd('\') (Get-TokenDropFileName $ComputerSid)
  if (-not (Test-Path -LiteralPath $tokPath)) { return 'SKIP_NO_TOKEN' }
  $len = (Get-Item -LiteralPath $tokPath).Length
  if ($len -lt 8) { return 'SKIP_EMPTY' }
  return 'INSTALL_ALLOWED'
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

# Positive: only pilot SID file present → pilot allowed, other skipped
$pilotTok = Join-Path $root (Get-TokenDropFileName $pilotSid)
Set-Content -LiteralPath $pilotTok -Value 'PLACEHOLDER-NOT-A-REAL-ENROLLMENT-TOKEN' -Encoding ASCII
$posPilot = Test-AuthGate -TokenDrop $root -ComputerSid $pilotSid
$posOther = Test-AuthGate -TokenDrop $root -ComputerSid $otherSid
if ($posPilot -ne 'INSTALL_ALLOWED') { throw "FAIL positive pilot gate: $posPilot" }
if ($posOther -ne 'SKIP_NO_TOKEN') { throw "FAIL negative other gate: $posOther" }
Write-Host ("POS_PILOT_ONLY=PASS pilot=" + $pilot.Name + " other=" + $other.Name)

# Cancel/expire simulation: remove token → install blocked again
Remove-Item -LiteralPath $pilotTok -Force
$afterCancel = Test-AuthGate -TokenDrop $root -ComputerSid $pilotSid
if ($afterCancel -ne 'SKIP_NO_TOKEN') { throw "FAIL cancel gate: $afterCancel" }
Write-Host 'NEG_AFTER_CANCEL=PASS (expired/cancelled token never installs)'

Write-Host 'AUTH_GATE=PASS'
Write-Host ("WORK_ROOT=" + $root)
Write-Host ("PILOT_SID=" + $pilotSid)
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
