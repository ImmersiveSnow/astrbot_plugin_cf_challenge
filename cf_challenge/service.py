"""群挑战流程，同群操作互斥，不同群可以独立进行。"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import Awaitable, Callable

from .codeforces import Codeforces
from .llm import Evaluator
from .models import ChallengeError, ProblemRange, Review, Round, UnsupportedStatement
from .presentation import hide_identity
from .storage import Store


class Challenges:
    def __init__(
        self,
        store: Store,
        codeforces: Codeforces,
        default_range: ProblemRange,
        cooldown: int = 30,
        max_solution: int = 6000,
    ):
        self.store = store
        self.codeforces = codeforces
        self.default_range = default_range
        self.cooldown = cooldown
        self.max_solution = max_solution
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, scope: str, *, wait: bool = False) -> asyncio.Lock:
        lock = self._locks.setdefault(scope, asyncio.Lock())
        if not wait and lock.locked():
            raise ChallengeError("本群正在出题或评审，请等这次处理完成后再试。")
        return lock

    async def start(
        self,
        scope: str,
        evaluator: Evaluator,
        deliver: Callable[[Round], Awaitable[None]],
    ) -> Round:
        async with self._lock(scope):
            if self.store.active(scope):
                raise ChallengeError(
                    "本群还有未完成的题。可用 /cur 重发题面，"
                    "或由管理员用 /giveup 放弃。"
                )
            selection = self.store.get_range(scope, self.default_range)
            seen = self.store.seen(scope)
            candidates = [
                problem
                for problem in await self.codeforces.problems()
                if selection.accepts(problem)
                and problem.key not in seen
                and (
                    not self.codeforces.local_only or self.codeforces.has_local(problem)
                )
            ]
            if not candidates:
                raise ChallengeError(
                    "当前范围内没有未出过的可用题。请调整范围；"
                    "如果启用了 local_only，还需要先导入相应题面。"
                )
            attempts = min(3, len(candidates))
            for problem in random.SystemRandom().sample(candidates, attempts):
                try:
                    source = await self.codeforces.statement(problem)
                except UnsupportedStatement:
                    continue
                break
            else:
                raise ChallengeError(
                    f"本次尝试的 {attempts} 道题均含暂不支持的图片、表格或公式格式，"
                    "未发布新题。请重新发送 /new、调整范围，"
                    "或导入可完整表达题意的本地题面。"
                )
            prepared = await evaluator.prepare(source)
            round_ = Round(
                uuid.uuid4().hex,
                scope,
                problem,
                source,
                prepared,
                created_at=time.time(),
            )
            self.store.save(round_)
            try:
                await deliver(round_)
            except BaseException:
                # 包括取消事件：发送中断后不能留下无法作答的活动题。
                round_.status = "interrupted"
                self.store.save(round_)
                raise
            round_.status = "active"
            self.store.save(round_)
            return round_

    async def submit(
        self, scope: str, sender: str, solution: str, evaluator: Evaluator
    ) -> tuple[Round, Review]:
        if not solution.strip():
            raise ChallengeError("请在 /submit 后写上具体解题思路。")
        if len(solution) > self.max_solution:
            raise ChallengeError(f"解法太长，请控制在 {self.max_solution} 字以内。")
        submitted_round = self.store.active(scope)
        if submitted_round is None:
            raise ChallengeError("本群还没有进行中的题，请先让管理员发送 /new。")
        # asyncio.Lock 按等待顺序唤醒；入队前绑定轮次，旧解答不能用于新题。
        async with self._lock(scope, wait=True):
            round_ = self.store.active(scope)
            if round_ is None or round_.id != submitted_round.id:
                raise ChallengeError(
                    "本轮已结束，你排队的这条补充无需再评审，也不会提交到新题。"
                )
            now = time.time()
            elapsed = now - round_.attempts.get(sender, 0)
            if elapsed < self.cooldown:
                raise ChallengeError(
                    f"请再等 {int(self.cooldown - elapsed) + 1} 秒后提交。"
                )
            round_.attempts[sender] = now
            history = list(round_.dialogues.get(sender, []))
            round_.dialogues.setdefault(sender, []).append(
                {"role": "user", "content": solution.strip()}
            )
            self.store.save(round_)
            review = await evaluator.judge(
                round_.source, round_.prepared, solution.strip(), history
            )
            if review.verdict == "accepted":
                if review.mode != "check" or review.complete is not True:
                    raise ChallengeError(
                        "评审结果不满足完整解法通过条件，本次未判定通过。"
                    )
                round_.status, round_.solver = "solved", sender
                round_.dialogues.clear()
            else:
                round_.dialogues[sender].append(
                    {
                        "role": "assistant",
                        "content": hide_identity(review.feedback, round_.problem),
                    }
                )
            self.store.save(round_)
            return round_, review

    async def reveal(self, scope: str) -> Round:
        async with self._lock(scope):
            round_ = self.store.active(scope)
            if round_ is None:
                raise ChallengeError("本群没有进行中的题。")
            round_.status = "revealed"
            round_.dialogues.clear()
            self.store.save(round_)
            return round_

    async def set_range(self, scope: str, selection: ProblemRange) -> None:
        async with self._lock(scope):
            self.store.set_range(scope, selection)
