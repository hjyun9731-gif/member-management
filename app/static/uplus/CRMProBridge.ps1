# Member Management -> LG U+ 통화매니저(CRM Pro) local bridge
# Runs only on this Windows PC and accepts requests from the member-management Railway site.
# IMPORTANT: this script never clicks the actual "지금 전송(Send)" button inside CRM Pro.
# It only opens the message screen and fills in the recipient number + message text.
# The user must press "지금 전송" inside CRM Pro themselves.
$ErrorActionPreference = 'Stop'
$BridgeVersion = '2.0.0'
$Port = 18765
$Prefix = "http://127.0.0.1:$Port/"
$AllowedOrigins = @(
    'https://member-management-production.up.railway.app'
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
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

$AppDir = Join-Path $env:LOCALAPPDATA 'MemberManagement\UPlusBridge'
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
$LogFile = Join-Path $AppDir 'bridge.log'
$SettingsFile = Join-Path $AppDir 'settings.json'

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

# ---------- settings (custom CRM Pro exe path, chosen once via tray menu) ----------
function Load-Settings {
    if (Test-Path $SettingsFile) {
        try { return (Get-Content $SettingsFile -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return [pscustomobject]@{ ExePath = '' } }
    }
    return [pscustomobject]@{ ExePath = '' }
}
function Save-Settings($settings) {
    try { $settings | ConvertTo-Json | Set-Content -Path $SettingsFile -Encoding UTF8 } catch { Log "SAVE_SETTINGS_FAIL $($_.Exception.Message)" }
}
$Script:Settings = Load-Settings

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

# ---------- CRM Pro process / install detection ----------
function Get-CrmProcess {
    $candidates = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.MainWindowHandle -ne 0 -and ($_.MainWindowTitle -match 'CRM\s*Pro|통화매니저|LG\s*U\+|LG\s*UPLUS|U\+\s*CRM')
    }
    return $candidates | Select-Object -First 1
}

function Find-CrmShortcut {
    $dirs = @(
        "$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
        "$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
        "$env:PUBLIC\Desktop",
        "$env:USERPROFILE\Desktop"
    )
    foreach ($d in $dirs) {
        if (!(Test-Path $d)) { continue }
        $lnk = Get-ChildItem -Path $d -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -match 'CRM\s*Pro|통화매니저|U\+\s*CRM' } | Select-Object -First 1
        if ($lnk) { return $lnk.FullName }
    }
    return $null
}

function Find-CrmExeInFolders {
    $roots = @(
        "$env:ProgramFiles",
        "${env:ProgramFiles(x86)}",
        "$env:LOCALAPPDATA\Programs"
    ) | Where-Object { $_ -and (Test-Path $_) }
    foreach ($root in $roots) {
        $hit = Get-ChildItem -Path $root -Filter '*.exe' -Recurse -Depth 3 -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match 'CRM\s*Pro|통화매니저|UPLUS|U\+' } | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Find-CrmExeInRegistry {
    $keys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    foreach ($k in $keys) {
        $entries = Get-ItemProperty -Path $k -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match 'CRM\s*Pro|통화매니저|U\+\s*CRM|LG\s*U\+' }
        foreach ($e in $entries) {
            $loc = $e.InstallLocation
            if ($loc -and (Test-Path $loc)) {
                $exe = Get-ChildItem -Path $loc -Filter '*.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($exe) { return $exe.FullName }
            }
            if ($e.DisplayIcon -and (Test-Path $e.DisplayIcon)) { return $e.DisplayIcon }
        }
    }
    return $null
}

# Returns the best-known path to the CRM Pro executable (or $null), checked in priority order:
# 1) user-selected path saved in settings.json, 2) Start Menu / Desktop shortcut target,
# 3) common Program Files search, 4) registry uninstall info.
function Resolve-CrmExePath {
    if ($Script:Settings.ExePath -and (Test-Path $Script:Settings.ExePath)) { return $Script:Settings.ExePath }
    $lnk = Find-CrmShortcut
    if ($lnk) {
        try {
            $sh = New-Object -ComObject WScript.Shell
            $target = $sh.CreateShortcut($lnk).TargetPath
            if ($target -and (Test-Path $target)) { return $target }
        } catch {}
    }
    $inFolders = Find-CrmExeInFolders
    if ($inFolders) { return $inFolders }
    $inRegistry = Find-CrmExeInRegistry
    if ($inRegistry) { return $inRegistry }
    return $null
}

function Start-CrmPro {
    $p = Get-CrmProcess
    if ($p) { return $p }
    $exe = Resolve-CrmExePath
    $lnk = Find-CrmShortcut
    try {
        if ($lnk) { Start-Process $lnk }
        elseif ($exe) { Start-Process $exe }
        else { return $null }
    } catch { Log "START_CRM_FAIL $($_.Exception.Message)"; return $null }
    for ($i=0;$i -lt 24;$i++) {
        Start-Sleep -Milliseconds 250
        $p = Get-CrmProcess
        if ($p) { return $p }
    }
    return $null
}

# ---------- UI Automation helpers (no hardcoded screen coordinates) ----------
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

# Opens CRM Pro's SMS screen and fills recipient + message.
# Deliberately never invokes the actual send/전송 button — that click is left to the user.
function Invoke-CrmProMessage([string]$phone, [string]$message) {
    $phone = ($phone -replace '\D','')
    if ($phone -notmatch '^01[016789]\d{7,8}$') { return @{ok=$false; prepared=$false; message='휴대폰번호 형식이 올바르지 않습니다.'} }
    if ([string]::IsNullOrWhiteSpace($message)) { return @{ok=$false; prepared=$false; message='문자 내용이 비어 있습니다.'} }

    # Always leave a safe clipboard fallback in case UI Automation can't find the fields.
    Set-Clipboard -Value ("수신번호: {0}`r`n`r`n{1}" -f $phone,$message)

    $proc = Start-CrmPro
    if (!$proc) { return @{ok=$false; prepared=$false; clipboard=$true; message='LG U+ 통화매니저 실행창을 찾지 못했습니다. 번호와 내용은 클립보드에 복사했습니다.'} }

    [Win32Focus]::ShowWindow($proc.MainWindowHandle,9) | Out-Null
    [Win32Focus]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
    Start-Sleep -Milliseconds 250
    $root = [Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle)

    # Open SMS/message area if the button is exposed through Windows UI Automation.
    [void](Invoke-ButtonByName $root '^(문자|SMS|메시지|문자메시지|문자보내기)$')
    Start-Sleep -Milliseconds 350
    $proc = Get-CrmProcess
    if ($proc) { $root = [Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle) }

    $phoneSet = Set-ControlValueByName $root '(수신|받는|휴대폰|전화).*(번호)?|recipient|mobile|phone' $phone
    $messageSet = Set-ControlValueByName $root '(문자.*내용|메시지.*내용|내용|message|content|text)' $message

    if (!($phoneSet -and $messageSet)) {
        return @{ok=$true; prepared=$false; clipboard=$true; crm_opened=$true; message='CRM Pro는 열었지만 자동입력할 필드를 정확히 찾지 못했습니다. 번호와 내용은 클립보드에 복사했습니다. 받는사람/내용칸에 붙여넣기 해주세요.'}
    }
    return @{ok=$true; prepared=$true; crm_opened=$true; message='U+ 통화매니저 문자창에 번호와 내용을 입력했습니다. 지금 전송 버튼은 직접 눌러주세요.'}
}

# ---------- HTTP listener (async so the tray UI thread stays responsive) ----------
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($Prefix)

function Handle-Request($ctx) {
    $req = $ctx.Request
    $origin = [string]$req.Headers['Origin']
    try {
        if (!(Is-OriginAllowed $origin)) { Write-JsonResponse $ctx 403 @{ok=$false;message='허용되지 않은 Origin입니다.'} ''; return }
        if ($req.HttpMethod -eq 'OPTIONS') {
            $ctx.Response.StatusCode = 204
            if ($origin) { $ctx.Response.Headers['Access-Control-Allow-Origin'] = $origin; $ctx.Response.Headers['Vary']='Origin' }
            $ctx.Response.Headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            $ctx.Response.Headers['Access-Control-Allow-Headers'] = 'Content-Type'
            $ctx.Response.Headers['Access-Control-Allow-Private-Network'] = 'true'
            $ctx.Response.OutputStream.Close(); return
        }
        $path = $req.Url.AbsolutePath
        if ($req.HttpMethod -eq 'GET' -and $path -eq '/health') {
            $crm = Get-CrmProcess
            $exePath = Resolve-CrmExePath
            Write-JsonResponse $ctx 200 @{ok=$true;product='LG U+ 통화매니저(CRM Pro)';version=$BridgeVersion;bridge_running=$true;crm_installed=[bool]($crm -or $exePath);crm_running=[bool]$crm;exe_path=$(if($exePath){$exePath}else{''})} $origin
            return
        }
        if ($req.HttpMethod -eq 'POST' -and $path -eq '/open-crm') {
            $p = Start-CrmPro
            Write-JsonResponse $ctx 200 @{ok=[bool]$p; crm_running=[bool]$p; message=$(if($p){'U+ 통화매니저를 실행했습니다.'}else{'U+ 통화매니저를 찾지 못했습니다. 트레이 메뉴에서 설치경로를 직접 선택해주세요.'})} $origin
            return
        }
        if ($req.HttpMethod -eq 'POST' -and $path -eq '/send') {
            $b = Read-BodyJson $req
            # auto_send from the web app is intentionally ignored — this bridge never presses the send button.
            $result = Invoke-CrmProMessage ([string]$b.phone) ([string]$b.message)
            Log ("SEND phone={0} prepared={1}" -f ([string]$b.phone),$result.prepared)
            Write-JsonResponse $ctx ($(if($result.ok){200}else{422})) $result $origin
            return
        }
        Write-JsonResponse $ctx 404 @{ok=$false;message='Not Found'} $origin
    } catch {
        try { Log "REQUEST_ERROR $($_.Exception.Message)"; Write-JsonResponse $ctx 500 @{ok=$false;message=$_.Exception.Message} $origin } catch {}
    }
}

function Begin-Accept {
    try { $listener.BeginGetContext({ param($ar)
            try {
                $ctx = $listener.EndGetContext($ar)
                Handle-Request $ctx
            } catch {} finally {
                if ($listener.IsListening) { Begin-Accept }
            }
        }, $null) | Out-Null
    } catch {}
}

try {
    $listener.Start()
    Log "START $Prefix version=$BridgeVersion"
} catch {
    Log "START_FAIL $($_.Exception.Message)"
    [Windows.Forms.MessageBox]::Show("U+ CRM Pro Bridge를 시작하지 못했습니다.`r`n먼저 'U+_CRMPro_연동설치.bat'를 관리자 권한으로 실행해주세요.`r`n`r`n$($_.Exception.Message)", 'U+ CRM Pro Bridge') | Out-Null
    exit 1
}
Begin-Accept

# ---------- system tray icon (runs on this thread's Windows Forms message loop) ----------
$trayIcon = New-Object System.Windows.Forms.NotifyIcon
$trayIcon.Icon = [System.Drawing.SystemIcons]::Application
$trayIcon.Text = 'U+ CRM Pro Bridge (회원관리 연동)'
$trayIcon.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$miOpen = New-Object System.Windows.Forms.ToolStripMenuItem 'U+ 통화매니저 실행'
$miOpen.Add_Click({ [void](Start-CrmPro); $trayIcon.ShowBalloonTip(2000,'U+ CRM Pro Bridge','U+ 통화매니저 실행을 시도했습니다.','Info') })
$menu.Items.Add($miOpen) | Out-Null

$miPath = New-Object System.Windows.Forms.ToolStripMenuItem '설치경로 직접 선택...'
$miPath.Add_Click({
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = '실행 파일 (*.exe)|*.exe'
    $dlg.Title = 'U+ 통화매니저(CRM Pro) 실행파일 선택'
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $Script:Settings.ExePath = $dlg.FileName
        Save-Settings $Script:Settings
        $trayIcon.ShowBalloonTip(2000,'U+ CRM Pro Bridge','설치경로를 저장했습니다.','Info')
    }
})
$menu.Items.Add($miPath) | Out-Null

$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

$miLog = New-Object System.Windows.Forms.ToolStripMenuItem '로그 폴더 열기'
$miLog.Add_Click({ Start-Process explorer.exe $AppDir })
$menu.Items.Add($miLog) | Out-Null

$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

$miExit = New-Object System.Windows.Forms.ToolStripMenuItem '종료'
$miExit.Add_Click({
    $trayIcon.Visible = $false
    try { $listener.Stop() } catch {}
    [System.Windows.Forms.Application]::Exit()
})
$menu.Items.Add($miExit) | Out-Null

$trayIcon.ContextMenuStrip = $menu
$trayIcon.Add_DoubleClick({ [void](Start-CrmPro) })

Log "TRAY_READY"
[System.Windows.Forms.Application]::Run()
try { $listener.Stop() } catch {}
Log "STOPPED"
