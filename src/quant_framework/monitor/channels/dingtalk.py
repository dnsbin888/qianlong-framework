"""DingTalk (钉钉) notification channel.

Sends messages via DingTalk robot webhook.
Setup: Add a bot to your DingTalk group and copy the webhook URL.
"""

from __future__ import annotations

from typing import Any

from quant_framework.monitor.notifier import WebhookNotifier


class DingTalkNotifier(WebhookNotifier):
    """钉钉机器人通知.

    Webhook URL format: https://oapi.dingtalk.com/robot/send?access_token=TOKEN

    Usage:
        notifier = DingTalkNotifier("https://oapi.dingtalk.com/robot/send?access_token=xxx")
        notifier.send("Trade Alert", "Bought 600000 @15.50", "info")
    """

    def __init__(self, webhook_url: str, secret: str = "") -> None:
        super().__init__("dingtalk", webhook_url, self._dingtalk_payload)
        self._secret = secret

    @staticmethod
    def _dingtalk_payload(title: str, content: str, level: str) -> dict[str, Any]:
        """DingTalk-specific Markdown payload."""
        return {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"## {title}\n\n{content}\n\n> Quant Framework",
            },
        }


class WeComNotifier(WebhookNotifier):
    """企业微信机器人通知.

    Webhook URL format: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=KEY
    """

    def __init__(self, webhook_url: str) -> None:
        super().__init__("wecom", webhook_url, self._wecom_payload)

    @staticmethod
    def _wecom_payload(title: str, content: str, level: str) -> dict[str, Any]:
        """WeCom Markdown payload."""
        return {
            "msgtype": "markdown",
            "markdown": {
                "content": f"## {title}\n{content}\n<font color=\"comment\">Quant Framework</font>",
            },
        }


class FeishuNotifier(WebhookNotifier):
    """飞书机器人通知.

    Webhook URL format: https://open.feishu.cn/open-apis/bot/v2/hook/HOOK_ID
    """

    def __init__(self, webhook_url: str) -> None:
        super().__init__("feishu", webhook_url, self._feishu_payload)

    @staticmethod
    def _feishu_payload(title: str, content: str, level: str) -> dict[str, Any]:
        """Feishu interactive card payload."""
        level_colors = {
            "info": "green",
            "warning": "yellow",
            "error": "red",
            "critical": "purple",
        }
        color = level_colors.get(level, "green")
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": color,
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                    {"tag": "hr"},
                    {"tag": "note", "elements": [{"tag": "plain_text", "content": "Quant Framework"}]},
                ],
            },
        }
