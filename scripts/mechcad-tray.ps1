#requires -Version 5.1
param(
  [switch]$NoBrowser,
  [int]$Port = 8001,
  [switch]$SkipShortcut,
  [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
$FrontendDir = Join-Path $Root "frontend"
$DistIndex = Join-Path $FrontendDir "dist\index.html"
$LogDir = Join-Path $Root "work\logs"
$LauncherLog = Join-Path $LogDir "mechcad-pro-launcher.log"
$IconPath = Join-Path $Root "assets\mechcad.ico"
$ScriptPath = Join-Path $PSScriptRoot "mechcad-tray.ps1"
$Url = "http://127.0.0.1:$Port/"
$HealthUrl = "http://127.0.0.1:$Port/api/health"
$MutexName = "MechCAD-Launcher-$Port"
$Script:BackendProcess = $null
$Script:StartedBackend = $false

if (-not (Test-Path -LiteralPath $LogDir)) {
  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}

function Write-LauncherLog([string]$Message) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  Add-Content -LiteralPath $LauncherLog -Value $line -Encoding UTF8
}

function Show-Message([string]$Text) {
  [System.Windows.Forms.MessageBox]::Show(
    $Text,
    "MechCAD IDE",
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Error
  ) | Out-Null
}

function Show-Balloon([string]$Title, [string]$Text) {
  $notify = New-Object System.Windows.Forms.NotifyIcon
  $notify.Icon = [System.Drawing.SystemIcons]::Information
  $notify.Visible = $true
  $notify.BalloonTipTitle = $Title
  $notify.BalloonTipText = $Text
  $notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
  $notify.ShowBalloonTip(3000)
  Start-Sleep -Milliseconds 2500
  $notify.Visible = $false
  $notify.Dispose()
}

function Test-Health {
  try {
    $response = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 2
    return ($response.status -eq "ok" -and $response.service -eq "mechcad-ide-api")
  } catch {
    return $false
  }
}

function Ensure-Shortcut {
  if ($SkipShortcut) {
    return
  }
  $desktop = [Environment]::GetFolderPath("Desktop")
  if (-not $desktop) {
    return
  }
  $lnk = Join-Path $desktop "MechCAD IDE.lnk"
  try {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($lnk)
    $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`""
    $shortcut.WorkingDirectory = $Root
    $shortcut.Description = "MechCAD IDE"
    if (Test-Path -LiteralPath $IconPath) {
      $shortcut.IconLocation = "$IconPath,0"
    }
    $shortcut.Save()
    Write-LauncherLog "shortcut refreshed: $lnk"
  } catch {
    Write-LauncherLog "shortcut creation failed: $($_.Exception.Message)"
  }
}

function Start-HiddenCommand([string]$CommandLine, [string]$WorkingDirectory) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $env:ComSpec
  $psi.Arguments = "/d /s /c `"$CommandLine`""
  $psi.WorkingDirectory = $WorkingDirectory
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  return [System.Diagnostics.Process]::Start($psi)
}

function Start-Backend {
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
  $outLog = Join-Path $LogDir "backend-pro-$Port-$stamp.out.log"
  $errLog = Join-Path $LogDir "backend-pro-$Port-$stamp.err.log"
  [Environment]::SetEnvironmentVariable("MECHCAD_PORT", [string]$Port)
  [Environment]::SetEnvironmentVariable("MECHCAD_HOST", "127.0.0.1")
  $quotedPython = "`"$PythonExe`""
  $quotedOut = "`"$outLog`""
  $quotedErr = "`"$errLog`""
  $commandLine = "$quotedPython -m backend.main > $quotedOut 2> $quotedErr"
  $process = Start-HiddenCommand $commandLine $Root
  $Script:BackendProcess = $process
  $Script:StartedBackend = $true
  Write-LauncherLog "backend started pid=$($process.Id) out=$outLog err=$errLog"
}

function Stop-Backend {
  if ($Script:BackendProcess -and -not $Script:BackendProcess.HasExited) {
    $targetPid = $Script:BackendProcess.Id
    try {
      & taskkill.exe /PID $targetPid /T /F 2>$null | Out-Null
    } catch {
      Write-LauncherLog "backend kill failed: $($_.Exception.Message)"
    }
    try {
      $Script:BackendProcess.WaitForExit(5000) | Out-Null
    } catch {
    }
  }
  $Script:BackendProcess = $null
  $Script:StartedBackend = $false
}

function Restart-BackendService {
  Stop-Backend
  Start-Sleep -Milliseconds 500
  Start-Backend
  $ready = $false
  for ($i = 0; $i -lt 120; $i++) {
    if (Test-Health) {
      $ready = $true
      break
    }
    Start-Sleep -Milliseconds 500
  }
  if (-not $ready) {
    Show-Message "服务重启失败，请查看日志目录：$LogDir"
  } else {
    Show-Balloon "MechCAD IDE" "服务已重启：$Url"
  }
}

if ($CheckOnly) {
  $check = [ordered]@{
    python = Test-Path -LiteralPath $PythonExe
    dist = Test-Path -LiteralPath $DistIndex
    port = $Port
    health = Test-Health
  }
  $check | ConvertTo-Json -Compress
  exit 0
}

$mutex = New-Object System.Threading.Mutex($false, $MutexName)
$hasHandle = $mutex.WaitOne(0)
if (-not $hasHandle) {
  if (Test-Health) {
    if (-not $NoBrowser -and $env:MECHCAD_OPEN_BROWSER -ne "0") {
      Start-Process $Url
    }
    Show-Balloon "MechCAD IDE" "MechCAD 已在运行，已打开界面"
  } else {
    Show-Message "MechCAD 已在运行，但服务尚未就绪。请稍后再试，或从系统托盘重启服务。"
  }
  $mutex.Dispose()
  exit 0
}

try {
  if (-not (Test-Path -LiteralPath $PythonExe)) {
    Show-Message "未找到 Python 环境：`n$PythonExe`n`n请先完成依赖安装，再启动 MechCAD。"
    exit 1
  }

  if (-not (Test-Path -LiteralPath $DistIndex)) {
    Show-Balloon "MechCAD IDE" "首次启动需要构建前端界面，请稍候..."
    Write-LauncherLog "frontend dist missing, building..."
    $buildOut = Join-Path $LogDir "frontend-build-$Port.out.log"
    $buildErr = Join-Path $LogDir "frontend-build-$Port.err.log"
    $quotedBuildOut = "`"$buildOut`""
    $quotedBuildErr = "`"$buildErr`""
    $buildCommand = "npm.cmd run build > $quotedBuildOut 2> $quotedBuildErr"
    $build = Start-HiddenCommand $buildCommand $FrontendDir
    $build.WaitForExit()
    if ($build.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $DistIndex)) {
      Show-Message "前端构建失败，请查看日志：`n$buildErr"
      exit 1
    }
    Write-LauncherLog "frontend build completed"
  }

  if (Test-Health) {
    Write-LauncherLog "reusing running backend on port $Port"
    Show-Balloon "MechCAD IDE" "已连接正在运行的服务：$Url"
  } else {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
      Show-Message "端口 $Port 已被其他程序占用。`n请关闭占用程序，或使用 -Port 指定其他端口。"
      exit 1
    }
    Start-Backend
    $ready = $false
    for ($i = 0; $i -lt 120; $i++) {
      if (Test-Health) {
        $ready = $true
        break
      }
      Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
      Show-Message "后端服务启动失败，请查看日志目录：$LogDir"
      Stop-Backend
      exit 1
    }
    Write-LauncherLog "backend ready on port $Port"
  }

  Ensure-Shortcut

  if (-not $NoBrowser -and $env:MECHCAD_OPEN_BROWSER -ne "0") {
    Start-Process $Url
  }

  $tray = New-Object System.Windows.Forms.NotifyIcon
  if (Test-Path -LiteralPath $IconPath) {
    try {
      $tray.Icon = New-Object System.Drawing.Icon($IconPath)
    } catch {
      $tray.Icon = [System.Drawing.SystemIcons]::Application
    }
  } else {
    $tray.Icon = [System.Drawing.SystemIcons]::Application
  }
  $tray.Text = "MechCAD IDE - 端口 $Port"
  $tray.Visible = $true

  $menu = New-Object System.Windows.Forms.ContextMenu
  $openItem = New-Object System.Windows.Forms.MenuItem("打开界面")
  $openItem.add_Click({ Start-Process $Url })
  $restartItem = New-Object System.Windows.Forms.MenuItem("重启服务")
  $restartItem.add_Click({ Restart-BackendService })
  $logItem = New-Object System.Windows.Forms.MenuItem("打开日志")
  $logItem.add_Click({ Start-Process explorer.exe $LogDir })
  $exitItem = New-Object System.Windows.Forms.MenuItem("退出")
  $exitItem.add_Click({
    Stop-Backend
    $tray.Visible = $false
    $tray.Dispose()
    $context.ExitThread()
  })
  $menu.MenuItems.AddRange(@($openItem, $restartItem, $logItem, $exitItem))
  $tray.ContextMenu = $menu

  Show-Balloon "MechCAD IDE" "MechCAD 已在后台运行：$Url"

  $context = New-Object System.Windows.Forms.ApplicationContext
  [System.Windows.Forms.Application]::Run($context)

  $tray.Visible = $false
  $tray.Dispose()
  Write-LauncherLog "launcher exited"
} finally {
  if ($hasHandle) {
    try { $mutex.ReleaseMutex() } catch {}
    $mutex.Dispose()
  }
}
