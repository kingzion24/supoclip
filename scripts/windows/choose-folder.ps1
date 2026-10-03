# Shows a Windows folder picker and prints the chosen path (nothing if cancelled).
# Used by katakata-start.bat to ask where Katakata should save your clips.
param([string]$Default = "")

Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "Choose the folder where Katakata saves your clips"
$dialog.ShowNewFolderButton = $true
if ($Default -and (Test-Path -LiteralPath $Default)) {
    $dialog.SelectedPath = $Default
}

# A hidden top-most form keeps the dialog in front of the console window.
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $dialog.SelectedPath
}
$owner.Dispose()
