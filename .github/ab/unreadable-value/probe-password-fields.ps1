# Probe real password fields: does UIA ValuePattern.Value fail while SetValue works?
# Runs under Windows PowerShell 5.1 (System.Windows.Automation client).
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

$winforms = @'
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = "ab-probe-winforms"
$t = New-Object System.Windows.Forms.TextBox
$t.UseSystemPasswordChar = $true
$t.Text = "prefill-"
$f.Controls.Add($t)
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({ [System.IO.File]::WriteAllText("$env:TEMP\ab-probe-winforms.txt", $t.Text) })
$timer.Start()
[System.Windows.Forms.Application]::Run($f)
'@

$wpf = @'
Add-Type -AssemblyName PresentationFramework
$w = New-Object System.Windows.Window
$w.Title = "ab-probe-wpf"
$w.Width = 300; $w.Height = 120
$p = New-Object System.Windows.Controls.PasswordBox
$p.Password = "prefill-"
$w.Content = $p
$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(500)
$timer.Add_Tick({ [System.IO.File]::WriteAllText("$env:TEMP\ab-probe-wpf.txt", $p.Password) })
$timer.Start()
$w.ShowDialog() | Out-Null
'@

function Probe([string]$title, [string]$script) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    $proc = Start-Process powershell.exe -ArgumentList "-STA", "-NoProfile", "-EncodedCommand", $encoded -PassThru
    try {
        $root = [System.Windows.Automation.AutomationElement]::RootElement
        $byName = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::NameProperty, $title)
        $win = $null
        for ($i = 0; $i -lt 100 -and $null -eq $win; $i++) {
            Start-Sleep -Milliseconds 200
            $win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $byName)
        }
        if ($null -eq $win) { throw "$title window not found" }
        $isEdit = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Edit)
        $edit = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $isEdit)
        $vp = $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        $read = try { "ok '" + $vp.Current.Value + "'" } catch { "FAILED " + $_.Exception.GetType().Name + ": " + $_.Exception.Message }
        $write = try { $vp.SetValue("typed"); "ok" } catch { "FAILED " + $_.Exception.GetType().Name + ": " + $_.Exception.Message }
        Start-Sleep -Milliseconds 1200
        $model = Get-Content "$env:TEMP\$title.txt" -ErrorAction SilentlyContinue
        Write-Host "[PROBE] $title IsPassword=$($edit.Current.IsPassword) get Value: $read | SetValue: $write | field now: '$model'"
    } finally {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}

Probe "ab-probe-winforms" $winforms
Probe "ab-probe-wpf" $wpf
