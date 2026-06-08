"""Notification manager — sends alerts via multiple channels.

Supports: DingTalk, WeCom (企业微信), Feishu (飞书), Email, Console.
Notifications are sent asynchronously to avoid blocking the main loop.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("quant_framework.notifier")


class BaseNotifier(ABC):
    """Abstract base for notification channels."""

    @abstractmethod
    async def send(self, title: str, content: str, level: str = "info") -> bool:
        """Send a notification.

        Args:
            title: Short title/summary.
            content: Full message body.
            level: Severity: 'info', 'warning', 'error', 'critical'.

        Returns:
            True if sent successfully.
        """

    @property
    @abstractmethod
    def name(self) -> str: ...


class ConsoleNotifier(BaseNotifier):
    """Print notifications to console."""

    @property
    def name(self) -> str:
        return "console"

    async def send(self, title: str, content: str, level: str = "info") -> bool:
        import sys

        color_codes = {
            "info": "\033[32m",
            "warning": "\033[33m",
            "error": "\033[31m",
            "critical": "\033[35m",
        }
        reset = "\033[0m"
        color = color_codes.get(level, "")

        print(f"\n{color}━━━ [{level.upper()}] {title} ━━━{reset}", file=sys.stderr)
        print(content, file=sys.stderr)
        print(f"{color}{'─' * 50}{reset}\n", file=sys.stderr)
        return True


class WebhookNotifier(BaseNotifier):
    """Generic webhook notifier for DingTalk, WeCom, Feishu.

    Each platform has slightly different payload formats, handled
    via the payload_builder callback.
    """

    def __init__(
        self,
        name: str,
        webhook_url: str,
        payload_builder: Any | None = None,
    ) -> None:
        self._name = name
        self._url = webhook_url
        self._build_payload = payload_builder or self._default_payload

    @property
    def name(self) -> str:
        return self._name

    @staticmethod
    def _default_payload(title: str, content: str, level: str) -> dict[str, Any]:
        """Default Markdown payload (works for DingTalk, Feishu)."""
        level_emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🚨", "critical": "🔥"}
        emoji = level_emoji.get(level, "📢")
        return {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"## {emoji} {title}\n\n{content}\n\n---\n*Quant Framework · {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
            },
        }

    async def send(self, title: str, content: str, level: str = "info") -> bool:
        if not self._url:
            return False

        try:
            import aiohttp

            payload = self._build_payload(title, content, level)
            async with aiohttp.ClientSession() as session:
                async with session.post(self._url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        return True
                    logger.warning("Webhook %s returned %d", self._name, resp.status)
                    return False
        except ImportError:
            logger.warning("aiohttp not installed, webhook notifications unavailable")
            return False
        except Exception as e:
            logger.error("Webhook %s send failed: %s", self._name, e)
            return False


class NotifierManager:
    """Manages multiple notification channels.

    Sends messages to all configured channels in parallel.
    Non-blocking — uses asyncio for concurrent HTTP calls.

    Usage:
        mgr = NotifierManager()
        mgr.add(ConsoleNotifier())
        mgr.add(WebhookNotifier("dingtalk", "https://oapi.dingtalk.com/robot/send?..."))
        await mgr.broadcast("Trade Alert", "Bought 600000 @15.50", "info")
    """

    def __init__(self) -> None:
        self._channels: list[BaseNotifier] = []

    def add(self, channel: BaseNotifier) -> None:
        """Add a notification channel."""
        self._channels.append(channel)
        logger.info("Notification channel added: %s", channel.name)

    def remove(self, name: str) -> bool:
        """Remove a channel by name."""
        for i, ch in enumerate(self._channels):
            if ch.name == name:
                self._channels.pop(i)
                return True
        return False

    async def broadcast(self, title: str, content: str, level: str = "info") -> dict[str, bool]:
        """Send notification to all channels concurrently.

        Args:
            title: Notification title.
            content: Message body.
            level: Severity level.

        Returns:
            Dict mapping channel name to success status.
        """
        if not self._channels:
            return {}

        results = await asyncio.gather(
            *[ch.send(title, content, level) for ch in self._channels],
            return_exceptions=True,
        )
        return {
            ch.name: (r is True)
            for ch, r in zip(self._channels, results)
        }

    def broadcast_sync(self, title: str, content: str, level: str = "info") -> dict[str, bool]:
        """Synchronous wrapper for broadcast (runs asyncio loop).

        Use this from non-async contexts (strategy callbacks, etc.).
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already in an async context — create task
                asyncio.create_task(self.broadcast(title, content, level))
                return {}
            return loop.run_until_complete(self.broadcast(title, content, level))
        except RuntimeError:
            return asyncio.run(self.broadcast(title, content, level))

    @property
    def channel_count(self) -> int:
        return len(self._channels)
