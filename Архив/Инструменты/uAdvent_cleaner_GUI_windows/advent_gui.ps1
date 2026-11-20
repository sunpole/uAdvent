# GUI launcher for uAdvent Cleaner. Windows PowerShell 5.1+, no extra modules.
$ErrorActionPreference = 'Stop'

try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()

    $engine = Join-Path $PSScriptRoot 'clean_advent.py'
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $defaultFolder = $PSScriptRoot
    if ((Test-Path -LiteralPath (Join-Path $projectRoot '001') -PathType Container) -or
        (Test-Path -LiteralPath (Join-Path $projectRoot '002') -PathType Container)) {
        $defaultFolder = $projectRoot
    }

    $window = New-Object System.Windows.Forms.Form
    $window.Text = 'uAdvent — очистка и объединение планов'
    $window.StartPosition = 'CenterScreen'
    $window.Size = New-Object System.Drawing.Size(790, 560)
    $window.MinimumSize = New-Object System.Drawing.Size(650, 480)
    $window.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $window.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Font

    $title = New-Object System.Windows.Forms.Label
    $title.Text = 'Очистка Markdown-планов YouVersion'
    $title.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 15)
    $title.Location = New-Object System.Drawing.Point(20, 15)
    $title.AutoSize = $true
    $window.Controls.Add($title)

    $hint = New-Object System.Windows.Forms.Label
    $hint.Text = 'Выберите папку одного плана (001 или 002) либо общую папку 021_uAdvent.'
    $hint.Location = New-Object System.Drawing.Point(22, 51)
    $hint.Size = New-Object System.Drawing.Size(735, 24)
    $hint.Anchor = 'Top,Left,Right'
    $window.Controls.Add($hint)

    $pathBox = New-Object System.Windows.Forms.TextBox
    $pathBox.Location = New-Object System.Drawing.Point(22, 85)
    $pathBox.Size = New-Object System.Drawing.Size(590, 28)
    $pathBox.Anchor = 'Top,Left,Right'
    $pathBox.Text = $defaultFolder
    $window.Controls.Add($pathBox)

    $choose = New-Object System.Windows.Forms.Button
    $choose.Text = 'Выбрать папку…'
    $choose.Location = New-Object System.Drawing.Point(626, 82)
    $choose.Size = New-Object System.Drawing.Size(135, 34)
    $choose.Anchor = 'Top,Right'
    $window.Controls.Add($choose)

    $start = New-Object System.Windows.Forms.Button
    $start.Text = 'Очистить и объединить'
    $start.Location = New-Object System.Drawing.Point(22, 130)
    $start.Size = New-Object System.Drawing.Size(215, 40)
    $window.Controls.Add($start)

    $open = New-Object System.Windows.Forms.Button
    $open.Text = 'Открыть выбранную папку'
    $open.Location = New-Object System.Drawing.Point(249, 130)
    $open.Size = New-Object System.Drawing.Size(225, 40)
    $window.Controls.Add($open)

    $status = New-Object System.Windows.Forms.Label
    $status.Text = 'Готово к запуску. Исходные .md не изменяются.'
    $status.Location = New-Object System.Drawing.Point(22, 185)
    $status.Size = New-Object System.Drawing.Size(739, 26)
    $status.Anchor = 'Top,Left,Right'
    $window.Controls.Add($status)

    $log = New-Object System.Windows.Forms.TextBox
    $log.Multiline = $true
    $log.ReadOnly = $true
    $log.ScrollBars = 'Both'
    $log.WordWrap = $false
    $log.Font = New-Object System.Drawing.Font('Consolas', 9.5)
    $log.Location = New-Object System.Drawing.Point(22, 218)
    $log.Size = New-Object System.Drawing.Size(739, 278)
    $log.Anchor = 'Top,Bottom,Left,Right'
    $log.Text = "Результат появится здесь.`r`n`r`nВ папке каждого плана будет создан каталог cleaned с общим Markdown и отчетом."
    $window.Controls.Add($log)

    $choose.Add_Click({
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = 'Выберите папку с планом или папку со всеми планами'
        $dialog.ShowNewFolderButton = $false
        if ([System.IO.Directory]::Exists($pathBox.Text)) {
            $dialog.SelectedPath = $pathBox.Text
        }
        if ($dialog.ShowDialog($window) -eq [System.Windows.Forms.DialogResult]::OK) {
            $pathBox.Text = $dialog.SelectedPath
            $status.Text = 'Папка выбрана. Нажмите «Очистить и объединить».'
        }
        $dialog.Dispose()
    })

    $open.Add_Click({
        $folder = $pathBox.Text.Trim().Trim('"')
        if ([System.IO.Directory]::Exists($folder)) {
            Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $folder + '"')
        } else {
            [System.Windows.Forms.MessageBox]::Show('Папка не найдена.', 'uAdvent') | Out-Null
        }
    })

    $start.Add_Click({
        $folder = $pathBox.Text.Trim().Trim('"')
        if (-not [System.IO.Directory]::Exists($folder)) {
            [System.Windows.Forms.MessageBox]::Show('Выбранная папка не существует.', 'uAdvent', 'OK', 'Warning') | Out-Null
            return
        }
        if (-not (Test-Path -LiteralPath $engine -PathType Leaf)) {
            [System.Windows.Forms.MessageBox]::Show('Не найден clean_advent.py рядом с этим интерфейсом.', 'uAdvent', 'OK', 'Error') | Out-Null
            return
        }
        $python = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        $arguments = ''
        if ($null -ne $python) {
            $arguments = '-3'
        } else {
            $python = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        }
        if ($null -eq $python) {
            [System.Windows.Forms.MessageBox]::Show('Python не найден. Установите Python 3.9+ с python.org.', 'uAdvent', 'OK', 'Error') | Out-Null
            return
        }

        $start.Enabled = $false
        $choose.Enabled = $false
        $window.Cursor = [System.Windows.Forms.Cursors]::WaitCursor
        $status.Text = 'Обрабатываются файлы…'
        $log.Text = "Папка: $folder`r`n`r`n"
        $window.Refresh()

        try {
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = $python.Source
            $psi.Arguments = ($arguments + ' "' + $engine + '" "' + $folder + '"').Trim()
            $psi.UseShellExecute = $false
            $psi.CreateNoWindow = $true
            $psi.RedirectStandardOutput = $true
            $psi.RedirectStandardError = $true
            $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
            $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
            $psi.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'

            $proc = New-Object System.Diagnostics.Process
            $proc.StartInfo = $psi
            [void]$proc.Start()
            $stdout = $proc.StandardOutput.ReadToEnd()
            $stderr = $proc.StandardError.ReadToEnd()
            $proc.WaitForExit()
            $exitCode = $proc.ExitCode
            $proc.Dispose()
            $log.Text += (($stdout + "`r`n" + $stderr).Trim() -replace "(?<!`r)`n", "`r`n")

            if ($exitCode -eq 0) {
                $status.Text = 'Готово! Все обнаруженные планы обработаны без предупреждений.'
            } elseif ($exitCode -eq 2) {
                $status.Text = 'Завершено с предупреждениями. Прочитайте отчет в cleaned.'
            } else {
                $status.Text = 'Не удалось обработать выбранную папку. Подробности ниже.'
            }
        } catch {
            $status.Text = 'Ошибка запуска. Подробности ниже.'
            $log.Text += "`r`nОШИБКА: $($_.Exception.Message)"
        } finally {
            $start.Enabled = $true
            $choose.Enabled = $true
            $window.Cursor = [System.Windows.Forms.Cursors]::Default
        }
    })

    [void]$window.ShowDialog()
} catch {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            "Не удалось запустить окно очистителя.`r`n$($_.Exception.Message)",
            'uAdvent', 'OK', 'Error'
        ) | Out-Null
    } catch {
        # Last-resort fallback for systems without Windows Forms.
        Write-Error $_
    }
}
