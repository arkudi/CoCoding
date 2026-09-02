"""Interactive native directory selection for the local Windows host."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class DirectoryPickerUnavailableError(RuntimeError):
    pass


class NativeDirectoryPicker:
    def select(self) -> str | None:
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
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Write($dialog.SelectedPath)
}
"""
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
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
