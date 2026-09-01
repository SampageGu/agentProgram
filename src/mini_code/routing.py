"""Deterministic M4.2 intent routing for safe Web turn selection."""

from __future__ import annotations

import re
from dataclasses import dataclass


REQUESTED_MODES = {"auto", "chat", "code"}
INTENTS = {"chat", "read", "code"}


@dataclass(frozen=True)
class RoutingDecision:
    requested_mode: str
    intent: str
    reason: str


class IntentRouter:
    """Prefer a non-mutating route unless the user clearly requests a change."""

    _SOCIAL = re.compile(
        r"^(你好|您好|嗨|哈[喽啰]|早上好|上午好|下午好|晚上好|hi|hello|hey|"
        r"谢谢|多谢|感谢|再见|拜拜|你是谁|你能做什么|介绍一下自己)[!！?？。.，,\s]*$",
        re.IGNORECASE,
    )
    _IMPERATIVE_CODE = re.compile(
        r"(?:请|帮我|麻烦|需要你|直接|现在)?\s*"
        r"(?:修复|修改|实现|新增|添加|删除|重构|补充|编写|创建|更新|替换|优化)"
    )
    _CODE_OBJECT = re.compile(
        r"(?:bug|代码|功能|接口|文件|测试|用例|实现|逻辑|页面|模块|函数|类)",
        re.IGNORECASE,
    )
    _ENGLISH_CODE = re.compile(
        r"^\s*(?:please\s+)?(?:fix|implement|add|update|refactor|delete|create|write)\b",
        re.IGNORECASE,
    )
    _INTERROGATIVE = re.compile(
        r"^\s*(?:如何|怎么|怎样|为什么|能否解释|请解释|how\s+(?:do|can|should)|why)",
        re.IGNORECASE,
    )
    _READ = re.compile(
        r"(?:项目|仓库|代码|源码|文件|函数|类|模块|入口|架构|调用链|测试|配置|"
        r"在哪里|在哪|定位|分析|解释|介绍|查看|看看|搜索|寻找|为什么|如何|怎么|"
        r"repository|codebase|source|file|function|class|module|architecture|explain|find|where)",
        re.IGNORECASE,
    )

    def route(self, message: str, requested_mode: str = "auto") -> RoutingDecision:
        mode = requested_mode.strip().lower()
        if mode not in REQUESTED_MODES:
            raise ValueError("mode must be auto, chat, or code")
        text = " ".join(message.strip().split())
        if not text:
            raise ValueError("message must not be empty")
        if mode == "chat":
            return RoutingDecision(mode, "chat", "user_selected_chat")
        if mode == "code":
            return RoutingDecision(mode, "code", "user_selected_code")
        if self._SOCIAL.fullmatch(text):
            return RoutingDecision(mode, "chat", "social_or_small_talk")
        if self._INTERROGATIVE.search(text) and self._READ.search(text):
            return RoutingDecision(mode, "read", "repository_question")
        if self._ENGLISH_CODE.search(text):
            return RoutingDecision(mode, "code", "explicit_code_change")
        imperative = self._IMPERATIVE_CODE.search(text)
        if imperative and (
            self._CODE_OBJECT.search(text)
            or imperative.group(0).strip().startswith(("修复", "修改", "实现", "重构"))
        ):
            return RoutingDecision(mode, "code", "explicit_code_change")
        if self._READ.search(text):
            return RoutingDecision(mode, "read", "repository_question")
        return RoutingDecision(mode, "chat", "safe_default")
