# Member Management -> LG U+ CRM Pro local bridge
# Runs only on this Windows PC and accepts requests from the member-management Railway site.
$ErrorActionPreference = 'Stop'
$BridgeVersion = '1.0.0'
$Port = 18765
$Prefix = "http://127.0.0.1:$Port/"
$AllowedOrigins = @(
    'https://member-management-production.up.railway.app'
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win32Focus {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@

$LogDir = Join-Path $env:LOCALAPPDATA 'MemberManagement\UPlusBridge'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir 'bridge.log'
function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Write-JsonResponse($ctx, [int]$status, $obj, [string]$origin='') {
    $json = $obj | ConvertTo-Json -Depth 8 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $ctx.Response.StatusCode = $status
    $ctx.Response.ContentType = 'application/json; charset=utf-8'
    $ctx.Response.Headers['Cache-Control'] = 'no-store'
    if ($origin) {
        $ctx.Response.Headers['Access-Control-Allow-Origin'] = $origin
        $ctx.Response.Headers['Vary'] = 'Origin'
    }
    $ctx.Response.Headers['Access-Control-Allow-Private-Network'] = 'true'
    $ctx.Response.ContentLength64 = $bytes.Length
    $ctx.Response.OutputStream.Write($bytes,0,$bytes.Length)
    $ctx.Response.OutputStream.Close()
}

function Is-OriginAllowed([string]$origin) {
    if ([string]::IsNullOrWhiteSpace($origin)) { return $true }
    if ($AllowedOrigins -contains $origin) { return $true }
    if ($origin -match '^https?://(localhost|127\.0\.0\.1)(:\d+)?$') { return $true }
    return $false
}

function Read-BodyJson($request) {
    $reader = New-Object IO.StreamReader($request.InputStream, $request.ContentEncoding)
    try { $body = $reader.ReadToEnd() } finally { $reader.Close() }
    if ([string]::IsNullOrWhiteSpace($body)) { return $null }
    return $body | ConvertFrom-Json
}

function Get-CrmProcess {
    $candidates = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.MainWindowHandle -ne 0 -and ($_.MainWindowTitle -match 'CRM\s*Pro|통화매니저|LG\s*U\+|LG\s*UPLUS')
    }
    return $candidates | Select-Object -First 1
}

function Start-CrmPro {
    $p = Get-CrmProcess
    if ($p) { return $p }
    $startDirs = @(
        "$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
        "$env:ProgramData\Microsoft\Windows\Start Menu\Programs"
    )
    foreach ($d in $startDirs) {
        if (!(Test-Path $d)) { continue }
        $lnk = Get-ChildItem -Path $d -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -match 'CRM\s*Pro|통화매니저' } | Select-Object -First 1
        if ($lnk) {
            Start-Process $lnk.FullName
            break
        }
    }
    for ($i=0;$i -lt 24;$i++) {
        Start-Sleep -Milliseconds 250
        $p = Get-CrmProcess
        if ($p) { return $p }
    }
    return $null
}

function Get-Descendants($root) {
    $cond = [Windows.Automation.Condition]::TrueCondition
    return $root.FindAll([Windows.Automation.TreeScope]::Descendants, $cond)
}

function Invoke-ButtonByName($root, [string]$regex) {
    foreach ($el in (Get-Descendants $root)) {
        if ($el.Current.ControlType -ne [Windows.Automation.ControlType]::Button) { continue }
        $name = [string]$el.Current.Name
        if ($name -match $regex) {
            try {
                $pat = $el.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
                $pat.Invoke(); return $true
            } catch {}
        }
    }
    return $false
}

function Set-ControlValueByName($root, [string]$regex, [string]$value) {
    foreach ($el in (Get-Descendants $root)) {
        $type = $el.Current.ControlType
        if ($type -ne [Windows.Automation.ControlType]::Edit -and $type -ne [Windows.Automation.ControlType]::Document) { continue }
        $key = "{0} {1}" -f ([string]$el.Current.Name),([string]$el.Current.AutomationId)
        if ($key -notmatch $regex) { continue }
        try {
            $pat = $el.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern)
            if (!$pat.Current.IsReadOnly) { $pat.SetValue($value); return $true }
        } catch {
            try {
                $el.SetFocus(); Start-Sleep -Milliseconds 80
                [Windows.Forms.SendKeys]::SendWait('^a')
                [Windows.Forms.SendKeys]::SendWait($value.Replace('{','{{}').Replace('}','{}}'))
                return $true
            } catch {}
        }
    }
    return $false
}

function Invoke-CrmProMessage([string]$phone, [string]$message, [bool]$autoSend) {
    $phone = ($phone -replace '\D','')
    if ($phone -notmatch '^01[016789]\d{7,8}$') { return @{ok=$false; sent=$false; prepared=$false; message='휴대폰번호 형식이 올바르지 않습니다.'} }
    if ([string]::IsNullOrWhiteSpace($message)) { return @{ok=$false; sent=$false; prepared=$false; message='문자 내용이 비어 있습니다.'} }

    # Always leave a safe clipboard fallback.
    Set-Clipboard -Value ("수신번호: {0}`r`n`r`n{1}" -f $phone,$message)

    $proc = Start-CrmPro
    if (!$proc) { return @{ok=$false; sent=$false; prepared=$false; clipboard=$true; message='LG U+ CRM Pro 실행창을 찾지 못했습니다. 번호와 내용은 클립보드에 복사했습니다.'} }

    [Win32Focus]::ShowWindow($proc.MainWindowHandle,9) | Out-Null
    [Win32Focus]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
    Start-Sleep -Milliseconds 250
    $root = [Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle)

    # Open SMS/message area if the button is exposed through Windows UI Automation.
    [void](Invoke-ButtonByName $root '^(문자|SMS|메시지|문자메시지)$')
    Start-Sleep -Milliseconds 350
    $proc = Get-CrmProcess
    if ($proc) { $root = [Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle) }

    $phoneSet = Set-ControlValueByName $root '(수신|받는|휴대폰|전화).*(번호)?|recipient|mobile|phone' $phone
    $messageSet = Set-ControlValueByName $root '(문자.*내용|메시지.*내용|내용|message|content|text)' $message

    if (!($phoneSet -and $messageSet)) {
        return @{ok=$true; sent=$false; prepared=$false; clipboard=$true; crm_opened=$true; message='CRM Pro는 열었지만 자동입력할 필드를 정확히 찾지 못했습니다. 번호와 내용은 클립보드에 복사했습니다.'}
    }

    if ($autoSend) {
        $clicked = Invoke-ButtonByName $root '^(전송|보내기|문자전송|SMS\s*전송)$'
        if ($clicked) {
            Start-Sleep -Milliseconds 250
            return @{ok=$true; sent=$true; prepared=$true; crm_opened=$true; message='CRM Pro 전송 버튼을 실행했습니다.'}
        }
    }
    return @{ok=$true; sent=$false; prepared=$true; crm_opened=$true; message='CRM Pro 문자작성창에 번호와 내용을 입력했습니다.'}
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($Prefix)
try {
    $listener.Start()
    Log "START $Prefix version=$BridgeVersion"
} catch {
    Log "START_FAIL $($_.Exception.Message)"
    [Windows.Forms.MessageBox]::Show("U+ CRM Pro Bridge를 시작하지 못했습니다.`r`n먼저 'U+_CRMPro_연동설치.bat'를 관리자 권한으로 실행해주세요.`r`n`r`n$($_.Exception.Message)", 'U+ CRM Pro Bridge') | Out-Null
    exit 1
}

while ($listener.IsListening) {
    try {
        $ctx = $listener.GetContext()
        $req = $ctx.Request
        $origin = [string]$req.Headers['Origin']
        if (!(Is-OriginAllowed $origin)) { Write-JsonResponse $ctx 403 @{ok=$false;message='허용되지 않은 Origin입니다.'} ''; continue }

        if ($req.HttpMethod -eq 'OPTIONS') {
            $ctx.Response.StatusCode = 204
            if ($origin) { $ctx.Response.Headers['Access-Control-Allow-Origin'] = $origin; $ctx.Response.Headers['Vary']='Origin' }
            $ctx.Response.Headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            $ctx.Response.Headers['Access-Control-Allow-Headers'] = 'Content-Type'
            $ctx.Response.Headers['Access-Control-Allow-Private-Network'] = 'true'
            $ctx.Response.OutputStream.Close(); continue
        }

        $path = $req.Url.AbsolutePath
        if ($req.HttpMethod -eq 'GET' -and $path -eq '/health') {
            $crm = Get-CrmProcess
            Write-JsonResponse $ctx 200 @{ok=$true;product='LG U+ CRM Pro';version=$BridgeVersion;crm_running=[bool]$crm} $origin
            continue
        }
        if ($req.HttpMethod -eq 'POST' -and $path -eq '/send') {
            $b = Read-BodyJson $req
            $result = Invoke-CrmProMessage ([string]$b.phone) ([string]$b.message) ([bool]$b.auto_send)
            Log ("SEND phone={0} sent={1} prepared={2}" -f ([string]$b.phone),$result.sent,$result.prepared)
            Write-JsonResponse $ctx ($(if($result.ok){200}else{422})) $result $origin
            continue
        }
        Write-JsonResponse $ctx 404 @{ok=$false;message='Not Found'} $origin
    } catch {
        try { Log "REQUEST_ERROR $($_.Exception.Message)"; Write-JsonResponse $ctx 500 @{ok=$false;message=$_.Exception.Message} $origin } catch {}
    }
}
