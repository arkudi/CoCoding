"""Interactive native directory selection for the local Windows host."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class DirectoryPickerUnavailableError(RuntimeError):
    pass


class NativeDirectoryPicker:
    def select(self, initial_path: str | Path | None = None) -> str | None:
        if os.name != "nt":
            raise DirectoryPickerUnavailableError(
                "Native directory selection is available only on Windows."
            )
        script = """
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择任务工作区'
$dialog.ShowNewFolderButton = $false
if ($env:COCODING_PICKER_INITIAL_DIRECTORY -and (Test-Path -LiteralPath $env:COCODING_PICKER_INITIAL_DIRECTORY -PathType Container)) {
    $dialog.SelectedPath = $env:COCODING_PICKER_INITIAL_DIRECTORY
}
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Write($dialog.SelectedPath)
}
"""
        environment = os.environ.copy()
        if initial_path is not None:
            try:
                resolved_initial = Path(initial_path).expanduser().resolve()
                if resolved_initial.is_dir():
                    environment["COCODING_PICKER_INITIAL_DIRECTORY"] = str(resolved_initial)
            except (OSError, RuntimeError):
                pass
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DirectoryPickerUnavailableError(
                "The Windows directory picker could not be opened."
            ) from error
        if completed.returncode != 0:
            raise DirectoryPickerUnavailableError(
                "The Windows directory picker could not be opened."
            )
        selected = completed.stdout.strip()
        if not selected:
            return None
        resolved = Path(selected).resolve()
        if not resolved.is_dir():
            raise DirectoryPickerUnavailableError(
                "The selected workspace directory is unavailable."
            )
        return str(resolved)
