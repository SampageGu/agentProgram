from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from mini_code.routing import IntentRouter


class IntentRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = IntentRouter()

    def test_social_messages_use_zero_tool_chat(self) -> None:
        for message in ["你好", "Hello!", "谢谢", "你能做什么？"]:
            with self.subTest(message=message):
                self.assertEqual(self.router.route(message).intent, "chat")

    def test_repository_questions_use_read_only_route(self) -> None:
        for message in [
            "项目入口在哪里？",
            "解释一下上下文压缩",
            "如何修复这个 bug？",
            "find the web module",
        ]:
            with self.subTest(message=message):
                self.assertEqual(self.router.route(message).intent, "read")

    def test_explicit_changes_use_coding_route(self) -> None:
        for message in [
            "修复 calculator.py 的除零 bug",
            "请帮我新增一个边界测试",
            "implement a health check",
        ]:
            with self.subTest(message=message):
                self.assertEqual(self.router.route(message).intent, "code")

    def test_manual_mode_overrides_auto_router(self) -> None:
        self.assertEqual(self.router.route("修复 bug", "chat").intent, "chat")
        self.assertEqual(self.router.route("你好", "code").intent, "code")
        with self.assertRaises(ValueError):
            self.router.route("hello", "unsafe")


if __name__ == "__main__":
    unittest.main()
