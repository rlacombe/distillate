"""macOS notifications via osascript."""

import logging
import platform
import subprocess

log = logging.getLogger(__name__)


def send(title: str, message: str) -> None:
    """Send a macOS notification. Silently no-ops on other platforms."""
    if platform.system() != "Darwin":
        log.debug("Notifications only supported on macOS, skipping")
        return

    try:
        subprocess.run(
            ["terminal-notifier", "-title", title, "-message", message,
             "-group", "distillate"],
            capture_output=True, timeout=10,
        )
    except FileNotFoundError:
        # Fallback to osascript if terminal-notifier not installed
        script = (
            f'display notification "{_escape(message)}" '
            f'with title "{_escape(title)}"'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=10,
            )
        except Exception as e:
            log.debug("Failed to send notification: %s", e)
    except Exception as e:
        log.debug("Failed to send notification: %s", e)


def notify_summary(sent_count: int, synced_count: int) -> None:
    """Send a summary notification if anything happened."""
    parts = []
    if sent_count:
        parts.append(f"{sent_count} sent to reMarkable")
    if synced_count:
        parts.append(f"{synced_count} synced back")

    if not parts:
        return

    send("Distillate", ", ".join(parts))


def _escape(s: str) -> str:
    """Escape for AppleScript string."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
