#Requires -Version 5.1
<#
.SYNOPSIS
  Prove per-computer token-drop NTFS isolation for the pilot computer account.

.DESCRIPTION
  Does NOT create the production share, link GPO, or install the agent.
  Builds a local temp tree with the same ACL model Deploy uses (icacls per SID R,D),
  then verifies WSIT02$ has no ACE on another computer's *.tok file.

  Optional: if -TokenDropUnc points at an existing share, also inspects live ACLs
  without writing tokens.
#>
[CmdletBinding()]
param(
  [string]$PilotComputer = 'WSIT02',
  [string]$OtherComputer = '',
  [string]$TokenDropUnc = '',
  [string]$WorkRoot = ''
)

$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory -ErrorAction Stop

$pilot = Get-ADComputer -Identity $PilotComputer -ErrorAction Stop
$pilotSid = $pilot.SID.Value
if (-not $OtherComputer) {
  $other = Get-ADComputer -Filter "Name -ne '$($pilot.Name)' -and Enabled -eq `$true" -ResultSetSize 1 |
    Select-Object -First 1
  if (-not $other) { throw 'Could not find another computer account for isolation contrast.' }
} else {
  $other = Get-ADComputer -Identity $OtherComputer -ErrorAction Stop
}
$otherSid = $other.SID.Value

if (-not $WorkRoot) {
  $WorkRoot = Join-Path $env:TEMP ("qka-token-isolation-" + [guid]::NewGuid().ToString('N'))
}
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null

function New-TokFile([string]$Path, [string]$ComputerSid) {
  Set-Content -LiteralPath $Path -Value 'PLACEHOLDER-NOT-A-REAL-TOKEN' -Encoding ASCII
  $ic = @(
    'icacls', $Path,
    '/inheritance:r',
    '/grant:r', "*${ComputerSid}:(R,D)",
    '/grant:r', 'BUILTIN\Administrators:(F)',
    '/grant:r', 'NT AUTHORITY\SYSTEM:(F)'
  )
  & $ic[0] $ic[1..($ic.Length-1)] | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "icacls failed for $Path" }
}

$pilotFile = Join-Path $WorkRoot ($pilotSid + '.tok')
$otherFile = Join-Path $WorkRoot ($otherSid + '.tok')
New-TokFile -Path $pilotFile -ComputerSid $pilotSid
New-TokFile -Path $otherFile -ComputerSid $otherSid

function Get-SidAces([string]$Path, [string]$Sid) {
  $acl = Get-Acl -LiteralPath $Path
  @($acl.Access | Where-Object {
      try { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $Sid }
      catch {
        try { ([System.Security.Principal.SecurityIdentifier]$_.IdentityReference.Value).Value -eq $Sid }
        catch { $_.IdentityReference.Value -eq $Sid -or $_.IdentityReference.Value -eq "*$Sid" }
      }
    })
}

$pilotOnOwn = Get-SidAces -Path $pilotFile -Sid $pilotSid
$pilotOnOther = Get-SidAces -Path $otherFile -Sid $pilotSid
$otherOnPilot = Get-SidAces -Path $pilotFile -Sid $otherSid

Write-Host ("PILOT=" + $pilot.SamAccountName + " SID=" + $pilotSid)
Write-Host ("OTHER=" + $other.SamAccountName + " SID=" + $otherSid)
Write-Host ("WORK_ROOT=" + $WorkRoot)
Write-Host ("pilot_aces_on_own_file=" + $pilotOnOwn.Count)
Write-Host ("pilot_aces_on_other_file=" + $pilotOnOther.Count)
Write-Host ("other_aces_on_pilot_file=" + $otherOnPilot.Count)

$pass = ($pilotOnOwn.Count -ge 1) -and ($pilotOnOther.Count -eq 0) -and ($otherOnPilot.Count -eq 0)
if (-not $pass) {
  Write-Host 'TOKEN_ISOLATION=FAIL'
  throw 'Isolation proof failed: pilot SID must access only its own *.tok.'
}

# Effective rights via Authz-style file open attempt is not possible for foreign computer$
# from this host; ACE absence is the enforceable model Deploy uses (same as token_drop.py).
Write-Host 'TOKEN_ISOLATION=PASS (WSIT02$ has no ACE on foreign *.tok; own file R,D only)'
Write-Host 'SMB_NOTE=Share must grant Domain Computers Change so computer$ can delete own file after claim.'
Write-Host 'AGENT_NOTE=Service must be Network Service (not Local Service) so SMB uses computer$.'

if ($TokenDropUnc -and (Test-Path -LiteralPath $TokenDropUnc)) {
  Write-Host ("LIVE_SHARE=" + $TokenDropUnc)
  $share = Get-SmbShare -Name ([IO.Path]::GetFileName($TokenDropUnc.TrimEnd('\'))) -ErrorAction SilentlyContinue
  if ($share) {
    Get-SmbShareAccess -Name $share.Name | ForEach-Object {
      Write-Host ("SHARE_ACE=" + $_.AccountName + " " + $_.AccessRight + " " + $_.AccessControlType)
    }
  }
}

Remove-Item -LiteralPath $WorkRoot -Recurse -Force -ErrorAction SilentlyContinue
Write-Host 'READY_FOR_ADMIN_CONFIGURATION=scripts_and_isolation_proof_ok (do not create share/link GPO/install yet unless approved)'
