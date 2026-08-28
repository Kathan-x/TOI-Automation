"""
Windows Notification Manager for TOI Daily (v2.0).
Displays beautiful, native Windows 10/11 toast notifications with formatted dates
and direct interactive click-to-open PDF actions.
"""

import datetime
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional


class NotificationManager:
    def __init__(self, enabled: bool = True, app_id: str = "Times of India Daily"):
        self.enabled = enabled
        self.app_id = app_id

    def notify_success(
        self,
        file_path: Path,
        target_date: datetime.date,
        edition: str = "Ahmedabad",
        logger: Optional[logging.Logger] = None
    ) -> None:
        """
        Shows a beautiful success toast notification:
        📰 Times of India — Ahmedabad
        Today's newspaper has been downloaded successfully.
        27 August 2026
        Saved to Desktop → TOI Daily
        """
        if not self.enabled:
            return

        date_human = target_date.strftime("%d %B %Y").lstrip("0")
        title = f"📰 Times of India — {edition}"
        message = (
            f"Today's newspaper has been downloaded successfully.\n"
            f"{date_human}\n"
            f"Saved to Desktop → TOI Daily"
        )

        self._send_toast(title, message, launch_path=file_path, logger=logger)

    def notify_failure(
        self,
        error_summary: str,
        edition: str = "Ahmedabad",
        logger: Optional[logging.Logger] = None
    ) -> None:
        """Shows a failure notification stating that a retry will occur automatically."""
        if not self.enabled:
            return

        title = f"⚠️ Times of India — {edition}"
        message = "Today's newspaper could not be downloaded right now. It will retry automatically."
        self._send_toast(title, message, logger=logger)

    def notify_update_available(self, new_version: str, logger: Optional[logging.Logger] = None) -> None:
        """Notifies the user of an available program update."""
        if not self.enabled:
            return

        title = "🚀 TOI Downloader Update Available"
        message = f"Version {new_version} is available. Visit GitHub to download the update."
        self._send_toast(title, message, logger=logger)

    def _send_toast(
        self,
        title: str,
        message: str,
        launch_path: Optional[Path] = None,
        logger: Optional[logging.Logger] = None
    ) -> None:
        """Sends native Windows toast notification via winotify with PowerShell fallback."""
        # 1. Try winotify
        try:
            from winotify import Notification
            toast = Notification(
                app_id=self.app_id,
                title=title,
                msg=message,
                duration="short"
            )
            if launch_path and launch_path.exists():
                toast.set_audio(sound="Default", loop=False)
                toast.add_actions(label="📖 Read Paper", launch=str(launch_path.resolve()))
            toast.show()
            if logger:
                logger.debug(f"Toast notification shown via winotify: '{title}'")
            return
        except Exception as e:
            if logger:
                logger.debug(f"winotify failed: {e}. Trying PowerShell fallback...")

        # 2. PowerShell Toast Fallback
        if sys.platform == "win32":
            self._send_powershell_toast(title, message, logger=logger)

    def _send_powershell_toast(self, title: str, message: str, logger: Optional[logging.Logger] = None) -> None:
        """Native Windows PowerShell Toast notification script."""
        try:
            # Escape single and double quotes
            safe_title = title.replace("'", "''").replace('"', '`"')
            safe_msg = message.replace("'", "''").replace('"', '`"').replace("\n", "`n")

            ps_script = f"""
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;
            [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;
            $xml = @"
<toast>
    <visual>
        <binding template="ToastGeneric">
            <text>{safe_title}</text>
            <text>{safe_msg}</text>
        </binding>
    </visual>
</toast>
"@;
            $doc = [Windows.Data.Xml.Dom.XmlDocument]::new();
            $doc.LoadXml($xml);
            $toast = [Windows.UI.Notifications.ToastNotification]::new($doc);
            $appId = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe';
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast);
            """
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                capture_output=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            if logger:
                logger.debug("PowerShell toast notification sent successfully.")
        except Exception as e:
            if logger:
                logger.warning(f"Could not display toast notification: {e}")
