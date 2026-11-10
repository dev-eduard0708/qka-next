<#
.SYNOPSIS
  Best-effort machine enrollment/renewal for QKAWebServer (SYSTEM scheduled task).
#>
$ErrorActionPreference = 'Continue'
$out = & certreq.exe -q -machine -enroll QKAWebServer 2>&1 | Out-String
$code = $LASTEXITCODE
Write-Output "CERTREQ_ENROLL_EXIT=$code"
Write-Output $out
# Always refresh IIS binding to newest enterprise leaf
& 'C:\QehcAI\QKA-Next\scripts\iis-agent-https\Update-QkaAgentIisBindingFromStore.ps1'
exit 0
