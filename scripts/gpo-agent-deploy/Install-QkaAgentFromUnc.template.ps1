# QKA Agent MSI bootstrap — NO enrollment secrets in this file.
# AUTH GATE: a per-SID *.tok is not enough. The API must confirm the token is
# unexpired and the deployment job is still scheduled (not cancelled).
# Progress: report started / msi_finished / failed with token+SID+machineProof.
# Pilot-group GPO only. Runs from a Scheduled Task as SYSTEM (no reboot required).
$ErrorActionPreference = 'Stop'
$msi = '@@MSI_UNC@@'
$url = '@@SERVER_URL@@'
$tokenDrop = '@@TOKEN_DROP@@'
$logDir = Join-Path $env:ProgramData 'QKA\DesktopAgent\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'gpo-install.log'
function Write-Log([string]$m) {
  # Never log token or proof values.
  Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format o), $m)
}
function Get-LocalComputerSid {
  # Scheduled task runs as SYSTEM: USERDOMAIN is often "NT AUTHORITY" and cannot
  # translate DOMAIN\COMPUTER$. Prefer bare COMPUTER$ / WMI domain lookup instead.
  $name = $env:COMPUTERNAME
  if (-not $name) {
    $name = (Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).Name
  }
  $sam = $name + '$'
  try {
    return (New-Object System.Security.Principal.NTAccount($sam)).Translate(
      [System.Security.Principal.SecurityIdentifier]
    ).Value
  } catch {
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
    foreach ($dom in @($cs.Domain, $env:USERDNSDOMAIN) | Select-Object -Unique) {
      if (-not $dom) { continue }
      if ($dom -match '^(?i)NT AUTHORITY|BUILTIN$') { continue }
      try {
        return (New-Object System.Security.Principal.NTAccount($dom, ($cs.Name + '$'))).Translate(
          [System.Security.Principal.SecurityIdentifier]
        ).Value
      } catch { }
    }
    throw
  }
}
function Get-TokenDropFileName([string]$sid) {
  return (($sid -replace '[^0-9A-Za-z\-]', '_') + '.tok')
}
function Get-MachineProof([string]$token, [string]$sid) {
  # Matches backend machine_proof_digest: HMAC-SHA256(token, "sid|") hex lowercase.
  $hmac = [System.Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($token))
  try {
    $hash = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes(($sid + '|')))
    $sb = New-Object System.Text.StringBuilder ($hash.Length * 2)
    foreach ($b in $hash) { [void]$sb.AppendFormat('{0:x2}', $b) }
    return $sb.ToString()
  } finally {
    $hmac.Dispose()
  }
}
function Send-InstallProgress {
  param(
    [string]$BaseUrl,
    [string]$Token,
    [string]$Sid,
    [string]$Proof,
    [string]$Phase,
    [Nullable[int]]$MsiExitCode = $null,
    [string]$Detail = ''
  )
  $uri = ($BaseUrl.TrimEnd('/')) + '/api/v1/agent/desktop/install-progress'
  $payload = @{
    token = $Token
    adComputerSid = $Sid
    machineProof = $Proof
    phase = $Phase
    detail = $Detail
  }
  if ($null -ne $MsiExitCode) { $payload.msiExitCode = [int]$MsiExitCode }
  $body = $payload | ConvertTo-Json -Compress
  Invoke-RestMethod -Uri $uri -Method POST -ContentType 'application/json' -Body $body -TimeoutSec 20 | Out-Null
}

$mutex = New-Object System.Threading.Mutex($false, 'Global\QKA-Agent-Install')
if (-not $mutex.WaitOne(0)) {
  Write-Log 'auth_gate_skip install_already_running'
  exit 0
}
try {
  $reg = 'HKLM:\SOFTWARE\QKA\DesktopAgent'
  New-Item -Path $reg -Force | Out-Null
  New-ItemProperty -Path $reg -Name 'TokenDropUnc' -Value $tokenDrop -PropertyType String -Force | Out-Null
  try {
    $computerSid = Get-LocalComputerSid
  } catch {
    Write-Log ('auth_gate_skip sid_resolve_failed ' + $_.Exception.Message)
    exit 0
  }

  $alreadyInstalled = [bool](Get-Service -Name 'QkaDesktopAgent' -ErrorAction SilentlyContinue)
  $tokName = Get-TokenDropFileName $computerSid
  $upgName = (($computerSid -replace '[^0-9A-Za-z\-]', '_') + '.upg')
  $tokPath = Join-Path $tokenDrop.TrimEnd('\') $tokName
  $upgPath = Join-Path $tokenDrop.TrimEnd('\') $upgName
  $mode = 'install'
  $tokenPath = $tokPath
  $authPath = '/api/v1/agent/desktop/install-authorization'
  $progressPath = '/api/v1/agent/desktop/install-progress'

  # Prefer .upg even if the service is missing (recovery after a failed MajorUpgrade rollback).
  if (Test-Path -LiteralPath $upgPath) {
    $mode = 'upgrade'
    $tokenPath = $upgPath
    $authPath = '/api/v1/agent/desktop/upgrade-authorization'
    $progressPath = '/api/v1/agent/desktop/upgrade-progress'
    Write-Log ('upgrade_candidate service_present=' + $alreadyInstalled)
  } elseif ($alreadyInstalled) {
    Write-Log 'already_installed no_upgrade_token'
    exit 0
  } elseif (-not (Test-Path -LiteralPath $tokPath)) {
    Write-Log ('auth_gate_skip absent file=' + $tokName)
    exit 0
  }

  try {
    $token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
  } catch {
    Write-Log ('auth_gate_skip token_read_failed ' + $_.Exception.Message)
    exit 0
  }
  if ($token.Length -lt 8) {
    Write-Log 'auth_gate_skip token_empty_or_invalid'
    exit 0
  }

  $proof = Get-MachineProof -token $token -sid $computerSid

  # Server confirms expiry and that cancel has not revoked the job. File presence is not enough.
  $authUri = ($url.TrimEnd('/')) + $authPath
  try {
    $body = @{
      token = $token
      adComputerSid = $computerSid
      machineProof = $proof
    } | ConvertTo-Json -Compress
    $resp = Invoke-RestMethod -Uri $authUri -Method POST -ContentType 'application/json' -Body $body -TimeoutSec 20
  } catch {
    Write-Log ('auth_gate_skip auth_api_failed ' + $_.Exception.Message)
    exit 0
  }
  if (-not $resp.authorized) {
    $why = [string]$resp.reason
    if (-not $why) { $why = 'denied' }
    Write-Log ('auth_gate_skip ' + $why)
    exit 0
  }
  Write-Log ('auth_gate_ok mode=' + $mode + ' reason=ok job=' + [string]$resp.jobId)

  function Send-Progress {
    param([string]$Phase, [Nullable[int]]$MsiExitCode = $null, [string]$Detail = '')
    $uri = ($url.TrimEnd('/')) + $progressPath
    $payload = @{
      token = $token
      adComputerSid = $computerSid
      machineProof = $proof
      phase = $Phase
      detail = $Detail
    }
    if ($null -ne $MsiExitCode) { $payload.msiExitCode = [int]$MsiExitCode }
    $pbody = $payload | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $uri -Method POST -ContentType 'application/json' -Body $pbody -TimeoutSec 20 | Out-Null
  }

  # Mark In Progress only when Windows actually starts install/upgrade work.
  try {
    Send-Progress -Phase 'started' -Detail ($mode + '_msiexec_begin')
    Write-Log 'progress_started_ok'
  } catch {
    Write-Log ('progress_started_failed ' + $_.Exception.Message)
    exit 0
  }

  if (-not (Test-Path -LiteralPath $msi)) {
    Write-Log ('msi_missing ' + $msi)
    try {
      Send-Progress -Phase 'failed' -MsiExitCode 2 -Detail 'msi_missing'
    } catch { Write-Log ('progress_failed_report_error ' + $_.Exception.Message) }
    exit 1
  }

  # Preserve ProgramData credentials/index across MSI major upgrade.
  $msiLog = Join-Path $logDir ('msiexec-' + $mode + '.log')
  # Stop running agent before MajorUpgrade so files unlock and StartServices is not racing.
  if ($alreadyInstalled) {
    try {
      Stop-Service -Name 'QkaDesktopAgent' -Force -ErrorAction Stop
      Write-Log 'service_stopped_before_msiexec'
      Start-Sleep -Seconds 2
    } catch {
      Write-Log ('service_stop_before_msiexec_failed ' + $_.Exception.Message)
    }
  }
  # MajorUpgrade (same UpgradeCode, higher ProductVersion) replaces binaries in-place.
  # ProgramData\QKA\DesktopAgent credentials/index are preserved (CreateFolder only).
  # MSI does not start the service (avoids Error 1920 rollback); we start it below.
  $arg = "/i `"$msi`" /qn /norestart QKASERVERURL=$url /L*v `"$msiLog`""
  Write-Log ('starting_msiexec mode=' + $mode)
  $p = Start-Process -FilePath msiexec.exe -ArgumentList $arg -Wait -PassThru
  $exitCode = [int]$p.ExitCode
  Write-Log ('msiexec_exit=' + $exitCode)

  if ($exitCode -ne 0 -and $exitCode -ne 3010) {
    try {
      Send-Progress -Phase 'failed' -MsiExitCode $exitCode -Detail ('msiexec_exit=' + $exitCode)
      Write-Log 'progress_failed_ok'
    } catch { Write-Log ('progress_failed_report_error ' + $_.Exception.Message) }
    exit $exitCode
  }

  $detail = if ($exitCode -eq 3010) { 'msiexec_exit=3010_restart_required' } else { 'msiexec_exit=0' }
  try {
    Send-Progress -Phase 'msi_finished' -MsiExitCode $exitCode -Detail $detail
    Write-Log ('progress_msi_finished_ok exit=' + $exitCode)
  } catch {
    Write-Log ('progress_msi_finished_failed ' + $_.Exception.Message)
  }

  # Prefer service SID ACE targeting for folder grants (does not change logon account).
  try {
    sc.exe sidtype QkaDesktopAgent unrestricted | Out-Null
    Write-Log 'service_sidtype_unrestricted'
  } catch {
    Write-Log ('service_sidtype_failed ' + $_.Exception.Message)
  }

  # Fresh MajorUpgrade can briefly refuse Start (SCM race). Retry before giving up.
  $started = $false
  foreach ($attempt in 1..6) {
    try {
      if ($attempt -gt 1) { Start-Sleep -Seconds 5 }
      Restart-Service -Name 'QkaDesktopAgent' -Force -ErrorAction Stop
      Write-Log ('service_restarted attempt=' + $attempt)
      $started = $true
      break
    } catch {
      try {
        Start-Service -Name 'QkaDesktopAgent' -ErrorAction Stop
        Write-Log ('service_started attempt=' + $attempt)
        $started = $true
        break
      } catch {
        Write-Log ('service_start_retry attempt=' + $attempt + ' ' + $_.Exception.Message)
      }
    }
  }
  if (-not $started) {
    Write-Log 'service_start_failed_after_retries'
  }

  # Consume the drop file after successful msiexec so the task does not loop.
  try {
    if (Test-Path -LiteralPath $tokenPath) {
      Remove-Item -LiteralPath $tokenPath -Force -ErrorAction Stop
      Write-Log ('drop_consumed mode=' + $mode)
    } else {
      Write-Log ('drop_already_absent mode=' + $mode)
    }
  } catch {
    Write-Log ('drop_consume_failed mode=' + $mode + ' ' + $_.Exception.Message)
  }

  # 3010 means success needing reboot — do not reboot here; heartbeat/version proves upgrade.
  exit 0
} finally {
  try { [void]$mutex.ReleaseMutex() } catch { }
  $mutex.Dispose()
}
