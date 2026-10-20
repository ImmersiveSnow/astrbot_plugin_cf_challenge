"""AstrBot 入口。安装后用 /cf 帮助 查看命令。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Node, Nodes, Plain
from astrbot.api.star import Context, Star, StarTools

from .cf_challenge.codeforces import Codeforces
from .cf_challenge.commands import parse_range
from .cf_challenge.llm import Evaluator
from .cf_challenge.models import ChallengeError, ProblemRange, Round
from .cf_challenge.presentation import chunks, reveal_text, review_text, statement_nodes
from .cf_challenge.rendering import render_statement
from .cf_challenge.service import Challenges
from .cf_challenge.storage import Store

HELP = """CF 群聊解题挑战
/new：从本群范围随机抽取新题
/cur：重发本轮中文题面
/cf 范围：查看本群选题条件
/cf 范围 1200 1800：修改难度，保留其他条件
/cf 范围 {"tags":["dp"],"exclude_tags":[],"min_contest":1500,"max_contest":2200}
JSON 也支持 min_rating、max_rating；未填写的条件保留原值。
tags 中的标签需全部满足；带空格的标签也可以放在 JSON 字符串内。
/submit <sol>：提交思路、补充修正或请求提示，不需要 @机器人
/giveup：放弃本轮并公布原题来源
默认仅 AstrBot 管理员及 QQ 群主／管理员能出题、改范围、放弃本轮。
同群解答按提交顺序排队；本轮通过后不再评审剩余提交。
每人提交有冷却时间；同轮同用户可直接补充或修正，也可提交“不会”请求提示。"""


class CFChallengePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        directory = StarTools.get_data_dir("astrbot_plugin_cf_challenge")
        self.store = Store(directory)
        self.codeforces = Codeforces(
            directory, local_only=bool(config.get("local_only", False))
        )
        self.challenges = Challenges(
            self.store,
            self.codeforces,
            ProblemRange.from_dict(config.get("selection", {})),
            cooldown=max(0, int(config.get("cooldown_seconds", 30))),
            max_solution=max(100, int(config.get("max_solution_chars", 6000))),
        )
        self.model_slots = asyncio.Semaphore(2)
        self.statement_render_slots = asyncio.Semaphore(1)

    def _scope(self, event: AstrMessageEvent) -> str:
        # 直接用群号，避免 AstrBot 的按人隔离会话设置把同群拆成多轮。
        return f"{event.get_platform_id()}:group:{event.get_group_id()}"

    def _allowed(self, event: AstrMessageEvent) -> bool:
        group = str(event.get_group_id() or "")
        allowed = {str(x) for x in self.config.get("allowed_groups", [])}
        return bool(group) and (not allowed or group in allowed)

    def _control(self, event: AstrMessageEvent) -> None:
        if not self.config.get("admin_only_controls", True) or event.is_admin():
            return
        raw = event.message_obj.raw_message
        sender = raw.get("sender", {}) if isinstance(raw, Mapping) else {}
        if event.get_platform_name() == "aiocqhttp" and isinstance(sender, Mapping):
            if sender.get("role") in ("admin", "owner"):
                return
        raise ChallengeError("只有 AstrBot 管理员或 QQ 群主／管理员能执行这项操作。")

    @staticmethod
    def _claim(event: AstrMessageEvent) -> None:
        event.should_call_llm(False)
        event.stop_event()

    def _evaluator(self, event: AstrMessageEvent) -> Evaluator:
        async def generate(system: str, prompt: str) -> str:
            async with self.model_slots:
                try:
                    async with asyncio.timeout(
                        int(self.config.get("model_timeout_seconds", 120))
                    ):
                        provider = self.config.get("provider_id", "") or (
                            await self.context.get_current_chat_provider_id(
                                umo=event.unified_msg_origin
                            )
                        )
                        response = await self.context.llm_generate(
                            chat_provider_id=provider,
                            prompt=prompt,
                            system_prompt=system,
                            contexts=[],
                        )
                        return response.completion_text
                except TimeoutError as exc:
                    raise ChallengeError(
                        "模型处理超时，本次没有判定通过，请稍后重试。"
                    ) from exc
                except Exception as exc:
                    logger.error("CF 挑战模型调用失败（%s）", type(exc).__name__)
                    raise ChallengeError(
                        "模型调用失败，请管理员检查 AstrBot 的模型配置。"
                    ) from exc

        return Evaluator(generate)

    async def _say(self, event: AstrMessageEvent, text: str) -> None:
        for part in chunks(text):
            await event.send(event.plain_result(part))

    async def _deliver(self, event: AstrMessageEvent, round_: Round) -> None:
        if self.config.get("statement_images", True):
            try:
                async with self.statement_render_slots:
                    pages = await render_statement(round_)
            except Exception as exc:
                logger.warning("CF 挑战题面图片生成失败（%s）", type(exc).__name__)
                await self._say(event, "题面图片生成失败，将改用完整文字发送。")
            else:
                nodes = [
                    Node(
                        uin=str(event.get_self_id()),
                        name="算法挑战",
                        content=[Image.fromBytes(page)],
                    )
                    for page in pages
                ]
                if event.get_platform_name() == "aiocqhttp" and self.config.get(
                    "merged_forward", True
                ):
                    try:
                        await event.send(event.chain_result([Nodes(nodes)]))
                        return
                    except Exception as exc:
                        logger.warning(
                            "CF 挑战图片合并转发失败，改为逐张发送（%s）",
                            type(exc).__name__,
                        )
                        await self._say(event, "题面图片合并转发失败，将改为逐张发送。")
                for node in nodes:
                    await event.send(event.chain_result(node.content))
                return

        parts = statement_nodes(round_)
        if event.get_platform_name() == "aiocqhttp" and self.config.get(
            "merged_forward", True
        ):
            nodes = [
                Node(
                    uin=str(event.get_self_id()), name="算法挑战", content=[Plain(part)]
                )
                for part in parts
            ]
            try:
                await event.send(event.chain_result([Nodes(nodes)]))
                return
            except Exception as exc:
                logger.warning(
                    "CF 挑战合并转发失败，改用分段文字（%s）", type(exc).__name__
                )
        for part in parts:
            await event.send(event.plain_result(part))

    async def _submit(self, event: AstrMessageEvent, solution: str) -> None:
        round_, review = await self.challenges.submit(
            self._scope(event),
            str(event.get_sender_id()),
            solution,
            self._evaluator(event),
        )
        text = review_text(review, round_)
        if review.verdict == "accepted":
            await event.send(
                event.chain_result(
                    [
                        Plain("🎉恭喜"),
                        At(qq=str(event.get_sender_id())),
                        Plain(" " + text),
                    ]
                )
            )
        else:
            await self._say(event, text)

    async def _error(self, event: AstrMessageEvent, exc: Exception) -> None:
        if isinstance(exc, ChallengeError):
            await self._say(event, str(exc))
        else:
            # 不回显异常正文，避免它带有未揭晓题号、URL 或服务凭据。
            logger.error("CF 挑战处理失败（%s）", type(exc).__name__)
            await self._say(event, "处理失败，请管理员检查插件数据和日志后重试。")

    @filter.command("new")
    async def new(self, event: AstrMessageEvent):
        """从本群范围随机抽取新题。"""
        await self._execute(event, "new")

    @filter.command("cur")
    async def cur(self, event: AstrMessageEvent):
        """重发当前题目。"""
        await self._execute(event, "cur")

    @filter.command("submit")
    async def submit(self, event: AstrMessageEvent):
        """提交思路、补充修正或请求提示，无需 @机器人。"""
        parts = event.get_message_str().strip().split(maxsplit=1)
        await self._execute(event, "submit", parts[1] if len(parts) > 1 else "")

    @filter.command("giveup")
    async def giveup(self, event: AstrMessageEvent):
        """放弃当前题目并公布原题。"""
        await self._execute(event, "giveup")

    @filter.command("cf")
    async def cf(self, event: AstrMessageEvent):
        """查看帮助或管理本群选题范围。"""
        text = event.get_message_str().strip().lstrip("/")
        args = re.sub(r"^cf(?:\s+|$)", "", text, count=1).strip()
        parts = args.split(maxsplit=1)
        command = "范围" if parts and parts[0] == "范围" else "help"
        query = parts[1] if len(parts) > 1 else ""
        await self._execute(event, command, query)

    async def _execute(
        self, event: AstrMessageEvent, command: str, query: str = ""
    ) -> None:
        if not self._allowed(event):
            return
        self._claim(event)
        scope = self._scope(event)
        try:
            if command == "help":
                await self._say(event, HELP)
            elif command == "范围":
                current = self.store.get_range(scope, self.challenges.default_range)
                if query.strip():
                    self._control(event)
                    current = parse_range(query, current)
                    await self.challenges.set_range(scope, current)
                    await self._say(
                        event, "已更新，下次出题生效。\n" + current.describe()
                    )
                else:
                    await self._say(event, current.describe())
            elif command == "new":
                self._control(event)
                await self._say(event, "正在读取原题、翻译并校对，请稍候。")
                await self.challenges.start(
                    scope,
                    self._evaluator(event),
                    lambda round_: self._deliver(event, round_),
                )
            elif command == "cur":
                round_ = self.store.active(scope)
                if round_ is None:
                    raise ChallengeError("本群没有进行中的题。")
                await self._deliver(event, round_)
            elif command == "giveup":
                self._control(event)
                round_ = await self.challenges.reveal(scope)
                await self._say(event, "本轮已放弃。\n" + reveal_text(round_))
            elif command == "submit":
                await self._submit(event, query)
            else:
                await self._say(event, HELP)
        except Exception as exc:
            await self._error(event, exc)

    async def terminate(self):
        await self.codeforces.close()
        self.store.close()
