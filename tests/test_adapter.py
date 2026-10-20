"""用模拟 AstrBot 验证路由和消息结构；不声称完成 QQ 实机联调。"""

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    class Plain:
        def __init__(self, text):
            self.text = text

    class Image:
        @classmethod
        def fromBytes(cls, data):
            from base64 import b64encode

            return cls("base64://" + b64encode(data).decode("ascii"))

        def __init__(self, file):
            self.file = file

    class At:
        def __init__(self, qq):
            self.qq = qq

    class Node:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Nodes:
        def __init__(self, nodes):
            self.nodes = nodes

    class Star:
        def __init__(self, context):
            self.context = context

    def decorator(*_, **__):
        return lambda function: function

    modules = {
        "astrbot": {},
        "astrbot.api": {"AstrBotConfig": dict, "logger": logging.getLogger("test")},
        "astrbot.api.event": {
            "AstrMessageEvent": object,
            "filter": SimpleNamespace(
                command=decorator,
                event_message_type=decorator,
                EventMessageType=SimpleNamespace(GROUP_MESSAGE="group"),
            ),
        },
        "astrbot.api.star": {
            "Context": object,
            "Star": Star,
            "StarTools": SimpleNamespace(get_data_dir=lambda _: tmp_path),
        },
        "astrbot.api.message_components": {
            "Plain": Plain,
            "Image": Image,
            "At": At,
            "Node": Node,
            "Nodes": Nodes,
        },
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    renderer = ModuleType("plugin_under_test.cf_challenge.rendering")

    async def render_statement(_):
        return [b"PNG-page-1", b"PNG-page-2"]

    renderer.render_statement = render_statement
    monkeypatch.setitem(
        sys.modules, "plugin_under_test.cf_challenge.rendering", renderer
    )
    root = Path(__file__).resolve().parents[1]
    parent = ModuleType("plugin_under_test")
    parent.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, "plugin_under_test", parent)
    spec = importlib.util.spec_from_file_location(
        "plugin_under_test.main", root / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


class Event:
    def __init__(
        self, text="cf 帮助", *, group="123", sender="alice", role="member", at=None
    ):
        self.text, self.group, self.sender = text, group, sender
        self.message_obj = SimpleNamespace(raw_message={"sender": {"role": role}})
        self.unified_msg_origin = f"qq:group:{group}_{sender}"
        self.messages = at or []
        self.sent = []
        self.stopped, self.call_llm = False, True

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.sender

    def get_sender_name(self):
        return self.sender

    def get_platform_id(self):
        return "qq-instance"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_self_id(self):
        return "999"

    def get_message_str(self):
        return self.text

    def get_messages(self):
        return self.messages

    def is_admin(self):
        return False

    def should_call_llm(self, value):
        self.call_llm = value

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return text

    def chain_result(self, components):
        return components

    async def send(self, result):
        self.sent.append(result)


def test_command_permissions_and_group_ranges(adapter):
    async def scenario():
        plugin = adapter.CFChallengePlugin(object(), {})
        try:
            user = Event("cf 范围 1200 1800")
            await plugin.cf(user)
            assert "只有" in user.sent[0]
            assert user.stopped and not user.call_llm
            admin = Event("cf 范围 1200 1800", role="admin")
            await plugin.cf(admin)
            assert "1200～1800" in admin.sent[0]
            alice, bob = Event(sender="alice"), Event(sender="bob")
            assert plugin._scope(alice) == plugin._scope(bob)
            assert plugin._scope(alice) != plugin._scope(Event(group="456"))
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_at_target_and_command_not_submitted_twice(adapter, round_):
    async def scenario():
        plugin = adapter.CFChallengePlugin(object(), {})
        submitted = []

        async def submit(event, text):
            submitted.append(text)

        plugin._submit = submit
        event = Event("逐个求和", at=[adapter.At("999")])
        round_.scope = plugin._scope(event)
        plugin.store.save(round_)
        try:
            await plugin.on_solution(Event("普通聊天"))
            await plugin.on_solution(Event("问别人", at=[adapter.At("888")]))
            await plugin.on_solution(Event("cf 提交 求和", at=[adapter.At("999")]))
            assert submitted == []
            await plugin.on_solution(event)
            assert submitted == ["逐个求和"]
            assert not event.call_llm and event.stopped
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_text_forward_remains_available_when_images_disabled(adapter, round_):
    async def scenario():
        plugin = adapter.CFChallengePlugin(object(), {"statement_images": False})
        event = Event()
        try:
            await plugin._deliver(event, round_)
            assert len(event.sent) == 1
            forward = event.sent[0][0]
            assert isinstance(forward, adapter.Nodes)
            assert len(forward.nodes) >= 5
            assert all(
                isinstance(node.content[0], adapter.Plain) for node in forward.nodes
            )
            text = "\n".join(node.content[0].text for node in forward.nodes)
            assert "PRIVATE_REFERENCE" not in text
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_forward_failure_falls_back_to_each_rendered_image(adapter, round_):
    async def scenario():
        class ForwardUnavailable(Event):
            async def send(self, result):
                if isinstance(result, list) and isinstance(result[0], adapter.Nodes):
                    raise RuntimeError("forward unavailable")
                await super().send(result)

        plugin = adapter.CFChallengePlugin(object(), {})
        event = ForwardUnavailable()
        try:
            await plugin._deliver(event, round_)
            singles = [
                item[0]
                for item in event.sent
                if isinstance(item, list) and isinstance(item[0], adapter.Image)
            ]
            assert len(singles) == 2
            assert all(image.file.startswith("base64://") for image in singles)
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_render_failure_falls_back_to_complete_public_text(
    adapter, round_, monkeypatch
):
    async def scenario():
        async def fail(_):
            raise adapter.ChallengeError("safe failure; PRIVATE_REFERENCE $$$tex$$$")

        monkeypatch.setattr(adapter, "render_statement", fail)
        plugin = adapter.CFChallengePlugin(object(), {})
        event = Event()
        try:
            await plugin._deliver(event, round_)
            text_parts = []
            for item in event.sent:
                if isinstance(item, str):
                    text_parts.append(item)
                elif (
                    item
                    and isinstance(item, list)
                    and isinstance(item[0], adapter.Nodes)
                ):
                    text_parts.extend(
                        node.content[0].text
                        for node in item[0].nodes
                        if isinstance(node.content[0], adapter.Plain)
                    )
            text = "\n".join(text_parts)
            assert "给出" in text and "输入" in text and "输出" in text
            assert "PRIVATE_REFERENCE" not in text and "Hidden Identity" not in text
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_single_image_send_failure_propagates(adapter, round_):
    async def scenario():
        class ImageUnavailable(Event):
            async def send(self, result):
                raise RuntimeError("image send unavailable")

        plugin = adapter.CFChallengePlugin(object(), {"merged_forward": False})
        try:
            with pytest.raises(RuntimeError, match="image send unavailable"):
                await plugin._deliver(ImageUnavailable(), round_)
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_private_or_disallowed_group_is_ignored(adapter):
    async def scenario():
        plugin = adapter.CFChallengePlugin(object(), {"allowed_groups": ["123"]})
        try:
            for event in (Event(group=""), Event(group="456")):
                await plugin.cf(event)
                assert not event.sent and not event.stopped
        finally:
            await plugin.terminate()

    asyncio.run(scenario())
