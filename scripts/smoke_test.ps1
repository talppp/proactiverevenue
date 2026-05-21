# L1 SMS-phone smoke test — PowerShell.
# Runs 9 checks against a live Render deploy and prints PASS / FAIL.
#
# Usage:
#   .\scripts\smoke_test.ps1
#   .\scripts\smoke_test.ps1 -Url "https://other.onrender.com"
#   .\scripts\smoke_test.ps1 -SmsToken "..." -AdminToken "..."
#
# Exit code 0 = all green, 1 = at least one failure.

param(
    [string]$Url        = "https://apteker-router-l1.onrender.com",
    [string]$SmsToken   = "TaYpMgFK3guXy2dU6nkRbpjy_j1IH5s1briRlUuwXLg",
    [string]$AdminToken = "TYFesbM02AO0Ya-r2ql0M2VKcHWtMpyk6YhuAUhyKs4"
)

$ErrorActionPreference = "Stop"
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'

# Force TLS 1.2+ for older PowerShell on Windows 10.
[System.Net.ServicePointManager]::SecurityProtocol =
    [System.Net.SecurityProtocolType]::Tls12 -bor
    [System.Net.SecurityProtocolType]::Tls13

$script:results = @()

function Show-Json {
    param($Object)
    $Object | ConvertTo-Json -Depth 10
}

function Record {
    param([string]$Name, [bool]$Pass, [string]$Detail = "")
    $marker = if ($Pass) { "PASS" } else { "FAIL" }
    Write-Host "  -> $marker" -ForegroundColor $(if ($Pass) { "Green" } else { "Red" })
    if ($Detail) { Write-Host "     $Detail" }
    $script:results += [pscustomobject]@{ Name = $Name; Pass = $Pass; Detail = $Detail }
}

function Invoke-Api {
    <#
      Wraps Invoke-RestMethod and returns:
        @{ StatusCode = <int>; Body = <object|string>; Error = <string|null> }
      regardless of whether the call succeeded or threw.
    #>
    param(
        [string]$Method = "GET",
        [string]$Path,
        [hashtable]$Headers = @{},
        $Body = $null
    )
    $params = @{
        Uri             = "$Url$Path"
        Method          = $Method
        Headers         = $Headers
        UseBasicParsing = $true
    }
    if ($Body -ne $null) {
        $params.ContentType = "application/json"
        $params.Body = $Body
    }
    try {
        $resp = Invoke-WebRequest @params -ErrorAction Stop
        $parsed = try { $resp.Content | ConvertFrom-Json } catch { $resp.Content }
        return @{ StatusCode = [int]$resp.StatusCode; Body = $parsed; Error = $null }
    } catch [System.Net.WebException] {
        $sc = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { -1 }
        $raw = ""
        if ($_.Exception.Response) {
            $stream = $_.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $raw    = $reader.ReadToEnd()
        }
        $parsed = try { $raw | ConvertFrom-Json } catch { $raw }
        return @{ StatusCode = $sc; Body = $parsed; Error = $_.Exception.Message }
    } catch {
        # PowerShell 7+: Invoke-WebRequest throws HttpResponseException
        $sc = -1
        try { $sc = [int]$_.Exception.Response.StatusCode } catch {}
        $raw = ""
        try { $raw = $_.ErrorDetails.Message } catch {}
        $parsed = try { $raw | ConvertFrom-Json } catch { $raw }
        return @{ StatusCode = $sc; Body = $parsed; Error = $_.Exception.Message }
    }
}

Write-Host "================================================================"
Write-Host "  L1 SMS-phone deploy smoke test"
Write-Host "  Target: $Url"
Write-Host "  Started: $(Get-Date -Format o)"
Write-Host "================================================================"
Write-Host

# Per-run identifier so each invocation produces fresh msg_ids; otherwise
# the in-memory staging store on the server makes test 4 / test 9 fail
# with "duplicate=true" on the second run.
$runId    = [Guid]::NewGuid().ToString("N").Substring(0, 8)
$runStamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss") + "+00:00"
Write-Host "Run ID: $runId    Run timestamp: $runStamp"
Write-Host

# ----- 1. healthz ----------------------------------------------------------
Write-Host "[1/9] GET /healthz (public)"
$r = Invoke-Api -Path "/healthz"
Show-Json $r.Body
$ok = ($r.StatusCode -eq 200) -and ($r.Body.ok -eq $true) -and ($r.Body.layer -eq "l1_sms_phone")
Record "healthz" $ok "status=$($r.StatusCode)"
Write-Host

# ----- 2. unauth POST ------------------------------------------------------
Write-Host "[2/9] POST /webhooks/sms_phone WITHOUT bearer (expect 401)"
$r = Invoke-Api -Method POST -Path "/webhooks/sms_phone" -Body '{}'
Write-Host "  status=$($r.StatusCode) body=$($r.Body)"
Record "unauth-rejected" ($r.StatusCode -eq 401)
Write-Host

# ----- 3. wrong bearer -----------------------------------------------------
Write-Host "[3/9] POST /webhooks/sms_phone WITH wrong bearer (expect 401)"
$r = Invoke-Api -Method POST -Path "/webhooks/sms_phone" `
    -Headers @{ Authorization = "Bearer not-the-real-token" } -Body '{}'
Write-Host "  status=$($r.StatusCode) body=$($r.Body)"
Record "wrong-bearer-rejected" ($r.StatusCode -eq 401)
Write-Host

# ----- 4. happy path -------------------------------------------------------
Write-Host "[4/9] POST /webhooks/sms_phone (happy path)"
$payload = @{
    from_number       = "+14045551234"
    body              = "render smoke test run=$runId"
    received_at_phone = $runStamp
} | ConvertTo-Json -Compress
$r = Invoke-Api -Method POST -Path "/webhooks/sms_phone" `
    -Headers @{ Authorization = "Bearer $SmsToken" } -Body $payload
Show-Json $r.Body
$msgId = $r.Body.msg_id
$ok = ($r.StatusCode -eq 200) -and ($r.Body.status -eq "queued") -and ($r.Body.duplicate -eq $false) -and ($msgId.Length -eq 64)
Record "happy-path" $ok "msg_id=$msgId"
Write-Host

# ----- 5. idempotency ------------------------------------------------------
Write-Host "[5/9] POST same payload AGAIN (expect duplicate=true, same msg_id)"
$r = Invoke-Api -Method POST -Path "/webhooks/sms_phone" `
    -Headers @{ Authorization = "Bearer $SmsToken" } -Body $payload
Show-Json $r.Body
$ok = ($r.StatusCode -eq 200) -and ($r.Body.duplicate -eq $true) -and ($r.Body.msg_id -eq $msgId)
Record "idempotent" $ok
Write-Host

# ----- 6. malformed payload ------------------------------------------------
Write-Host "[6/9] POST malformed body (expect 422)"
$r = Invoke-Api -Method POST -Path "/webhooks/sms_phone" `
    -Headers @{ Authorization = "Bearer $SmsToken" } `
    -Body '{"from_number":"+1","body":""}'
Write-Host "  status=$($r.StatusCode)"
Record "malformed-rejected" ($r.StatusCode -eq 422)
Write-Host

# ----- 7. admin auth gating ------------------------------------------------
Write-Host "[7/9] GET /admin/sms_inbox_raw/health WITHOUT admin bearer (expect 401)"
$r = Invoke-Api -Path "/admin/sms_inbox_raw/health"
Write-Host "  status=$($r.StatusCode)"
Record "admin-unauth-rejected" ($r.StatusCode -eq 401)
Write-Host

# ----- 8. admin health -----------------------------------------------------
Write-Host "[8/9] GET /admin/sms_inbox_raw/health (with admin bearer)"
$r = Invoke-Api -Path "/admin/sms_inbox_raw/health" `
    -Headers @{ Authorization = "Bearer $AdminToken" }
Show-Json $r.Body
$ok = ($r.StatusCode -eq 200) -and ($r.Body.handed_off -ge 1)
Record "admin-health-counts" $ok "handed_off=$($r.Body.handed_off)"
Write-Host

# ----- 9. admin inject -----------------------------------------------------
Write-Host "[9/9] POST /admin/sms_inbox_raw/inject (manual backfill)"
$injectBody = @{
    from_number       = "+14045559999"
    body              = "admin backfill run=$runId"
    received_at_phone = $runStamp
} | ConvertTo-Json -Compress
$r = Invoke-Api -Method POST -Path "/admin/sms_inbox_raw/inject" `
    -Headers @{ Authorization = "Bearer $AdminToken" } -Body $injectBody
Show-Json $r.Body
$ok = ($r.StatusCode -eq 200) -and ($r.Body.status -eq "queued") -and ($r.Body.duplicate -eq $false)
Record "admin-inject" $ok
Write-Host

# ----- summary -------------------------------------------------------------
Write-Host "================================================================"
$passed = ($script:results | Where-Object Pass).Count
$total  = $script:results.Count
$failed = $total - $passed
Write-Host "  Summary: $passed/$total passed"
foreach ($r in $script:results) {
    $marker = if ($r.Pass) { "PASS" } else { "FAIL" }
    $color  = if ($r.Pass) { "Green" } else { "Red" }
    Write-Host ("  {0,-4}  {1}" -f $marker, $r.Name) -ForegroundColor $color
}
Write-Host "================================================================"

if ($failed -gt 0) { exit 1 } else { exit 0 }
