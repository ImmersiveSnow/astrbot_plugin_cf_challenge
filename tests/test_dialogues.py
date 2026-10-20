import asyncio
import json
from dataclasses import replace

import pytest

from cf_challenge.models import ChallengeError, ProblemRange, Review
from cf_challenge.service import Challenges
from cf_challenge.storage import Store


class Catalog:
    local_only = False

    def __init__(self, problem, source):
        self.problem, self.source = problem, source

    async def problems(self):
        return [self.problem]

    async def statement(self, _):
        return self.source

    def has_local(self, _):
        return True


class Evaluator:
    def __init__(self, prepared, judge):
        self.prepared, self.judge_fn = prepared, judge

    async def prepare(self, _):
        return self.prepared

    async def judge(self, source, prepared, solution, history):
        return await self.judge_fn(solution, history)


def run(coro):
    return asyncio.run(coro)


async def deliver(_):
    pass


def test_dialogue_history_isolated_by_user_group_and_persisted(
    tmp_path, problem, source, prepared
):
    async def scenario():
        store = Store(tmp_path)

        async def judge(solution, history):
            if solution == "partial":
                return Review("needs_more", "补充更新规则。", "private proof", "")
            if solution == "complete" and history == [
                {"role": "user", "content": "partial"},
                {"role": "assistant", "content": "补充更新规则。"},
            ]:
                return Review(
                    "accepted", "完整方案正确。", "secret", "O(n)", complete=True
                )
            return Review("needs_more", "仍需补充。", "", "")

        evaluator = Evaluator(prepared, judge)
        catalog = Catalog(problem, source)
        service = Challenges(store, catalog, ProblemRange(), cooldown=0)
        try:
            round_ = await service.start("group-a", evaluator, deliver)
            await service.submit("group-a", "alice", "partial", evaluator)
            # 重载后前文仍属于 Alice，且评审不包含 proof。
            store.close()
            store = Store(tmp_path)
            service = Challenges(store, catalog, ProblemRange(), cooldown=0)
            await service.submit("group-a", "bob", "complete", evaluator)
            assert store.active("group-a") is not None
            active = store.active("group-a")
            assert [m["content"] for m in active.dialogues["alice"]] == [
                "partial",
                "补充更新规则。",
            ]
            assert "private proof" not in repr(active.dialogues)
            await service.submit("group-a", "alice", "complete", evaluator)
            assert store.active("group-a") is None
            solved = store.db.execute(
                "SELECT data FROM rounds WHERE id=?", (round_.id,)
            ).fetchone()
            assert json.loads(solved[0])["dialogues"] == {}
            catalog.problem = replace(problem, contest_id=1001)
            await service.start("group-a", evaluator, deliver)
            await service.submit("group-a", "alice", "complete", evaluator)
            assert store.active("group-a") is not None
            await service.start("group-b", evaluator, deliver)
            await service.submit("group-b", "alice", "complete", evaluator)
            assert store.active("group-b") is not None
        finally:
            store.close()

    run(scenario())


@pytest.mark.parametrize("outcome", ["failure", "cancel"])
def test_failed_or_cancelled_judgement_keeps_only_user_submission(
    tmp_path, problem, source, prepared, outcome
):
    async def scenario():
        store = Store(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()

        async def interrupted(solution, history):
            if solution == "prior":
                return Review("needs_more", "先前反馈。", "secret", "")
            entered.set()
            await release.wait()
            if outcome == "failure":
                raise RuntimeError("model unavailable")
            return Review("needs_more", "not reached", "", "")

        service = Challenges(
            store, Catalog(problem, source), ProblemRange(), cooldown=0
        )
        evaluator = Evaluator(prepared, interrupted)
        task = None
        try:
            round_ = await service.start("group", evaluator, deliver)
            await service.submit("group", "alice", "prior", evaluator)
            task = asyncio.create_task(
                service.submit("group", "alice", "my attempt", evaluator)
            )
            await entered.wait()
            if outcome == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                with pytest.raises(RuntimeError, match="model unavailable"):
                    await task
            saved = store.active("group")
            assert saved.id == round_.id
            assert saved.dialogues == {
                "alice": [
                    {"role": "user", "content": "prior"},
                    {"role": "assistant", "content": "先前反馈。"},
                    {"role": "user", "content": "my attempt"},
                ]
            }
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            store.close()

    run(asyncio.wait_for(scenario(), 5))


def test_legacy_round_loads_and_reveal_clears_dialogues(
    tmp_path, problem, source, prepared
):
    async def scenario():
        store = Store(tmp_path)
        service = Challenges(store, Catalog(problem, source), ProblemRange())
        try:
            round_ = await service.start(
                "group", Evaluator(prepared, lambda *_: None), deliver
            )
            data = round_.to_dict()
            data.pop("dialogues")
            store.db.execute(
                "UPDATE rounds SET data=? WHERE id=?", (json.dumps(data), round_.id)
            )
            store.db.commit()
            legacy = store.active("group")
            assert legacy.dialogues == {}
            legacy.dialogues["alice"] = [{"role": "user", "content": "old"}]
            store.save(legacy)
            revealed = await service.reveal("group")
            assert revealed.dialogues == {}
            assert store.active("group") is None
            data = json.loads(
                store.db.execute(
                    "SELECT data FROM rounds WHERE id=?", (round_.id,)
                ).fetchone()[0]
            )
            assert data["dialogues"] == {}
        finally:
            store.close()

    run(scenario())


@pytest.mark.parametrize(
    "review",
    [
        Review("accepted", "partial", "", "", mode="hint", complete=True),
        Review("accepted", "partial", "", "", mode="check", complete=False),
    ],
)
def test_incomplete_or_hint_accepted_result_cannot_settle_round(
    tmp_path, problem, source, prepared, review
):
    async def scenario():
        store = Store(tmp_path)
        service = Challenges(
            store, Catalog(problem, source), ProblemRange(), cooldown=0
        )

        async def judge(solution, history):
            if solution == "prior":
                return Review("needs_more", "保留的公开提示。", "secret", "")
            return review

        evaluator = Evaluator(prepared, judge)
        try:
            round_ = await service.start("group", evaluator, deliver)
            await service.submit("group", "alice", "prior", evaluator)
            with pytest.raises(ChallengeError):
                await service.submit("group", "alice", "claim", evaluator)
            active = store.active("group")
            assert active.id == round_.id and active.status == "active"
            assert active.solver == ""
            assert active.dialogues["alice"] == [
                {"role": "user", "content": "prior"},
                {"role": "assistant", "content": "保留的公开提示。"},
                {"role": "user", "content": "claim"},
            ]
        finally:
            store.close()

    run(scenario())
