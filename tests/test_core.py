import asyncio
import json
from dataclasses import replace

import pytest

from cf_challenge.codeforces import parse_statement
from cf_challenge.commands import parse_range
from cf_challenge.llm import Evaluator, parse_json
from cf_challenge.models import ChallengeError, Problem, ProblemRange, Review
from cf_challenge.presentation import hide_identity, review_text, statement_nodes
from cf_challenge.service import Challenges
from cf_challenge.storage import Store

HTML = """<div class="problem-statement">
<div class="header"><div class="title">A. Secret Name</div>
<div class="time-limit"><div class="property-title">time limit</div>1 second</div>
<div class="memory-limit"><div class="property-title">memory limit</div>256 MB</div>
</div><div><p>Find the sum of $$$n$$$ integers.</p>
<p>Negative numbers are allowed.</p></div>
<div class="input-specification"><div class="section-title">Input</div>
Two integers.</div>
<div class="output-specification"><div class="section-title">Output</div>The sum.</div>
<div class="sample-tests"><div class="input">
<pre><div class="test-example-line">2</div><div
class="test-example-line">1 2</div></pre></div>
<div class="output"><pre>3<br>4</pre></div></div>
<div class="note"><div class="section-title">Note</div>A small example.</div></div>"""


def run(awaitable):
    return asyncio.run(awaitable)


class Generator:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, system, prompt):
        self.calls.append((system, json.loads(prompt)))
        value = self.responses.pop(0)
        if callable(value):
            value = value(self.calls[-1][1])
        return (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )


def verdict(value="accepted", **changes):
    return {
        "verdict": value,
        "mode": "check",
        "complete": value == "accepted",
        "feedback": "逐个累加能得到所有数的和。",
        "proof": "每个输入恰好计入一次。",
        "complexity": "O(n) 时间，O(1) 额外空间。",
        "counterexample": "",
        **changes,
    }


def test_filter_all_tags_exclusion_and_inclusive_bounds(problem):
    selection = ProblemRange(1200, 1200, ("dp", "math"), (), 1000, 1000)
    assert selection.accepts(problem)
    assert not replace(selection, tags=("graphs",)).accepts(problem)
    assert not replace(selection, exclude_tags=("math",), tags=("dp",)).accepts(problem)
    assert not replace(selection, max_contest=999, min_contest=1).accepts(problem)
    assert not replace(selection, max_rating=1100, min_rating=800).accepts(problem)


@pytest.mark.parametrize(
    "value",
    [
        {"min_rating": 1600, "max_rating": 800},
        {"tags": "dp"},
        {"tags": [1]},
        {"tags": ["dp"], "exclude_tags": ["dp"]},
        {"min_contest": 0},
    ],
)
def test_invalid_ranges_rejected(value):
    with pytest.raises(ChallengeError):
        ProblemRange.from_dict(value)


def test_range_updates_preserve_unmentioned_fields():
    base = ProblemRange(tags=("binary search",), min_contest=1500)
    assert parse_range("1200 1900", base).tags == ("binary search",)
    updated = parse_range('{"tags":["dp"],"max_contest":2000}', base)
    assert updated.min_contest == 1500 and updated.max_contest == 2000
    with pytest.raises(ChallengeError):
        parse_range('{"rating":1000}', base)


def test_api_problem_without_rating_or_unsafe_index_is_skipped():
    raw = {
        "contestId": 1000,
        "index": "A",
        "name": "Sum",
        "type": "PROGRAMMING",
        "tags": [],
    }
    assert Problem.from_api(raw) is None
    assert Problem.from_api({**raw, "rating": 1000, "index": "../../"}) is None


def test_parse_html_preserves_formula_samples_and_limits():
    source = parse_statement(HTML)
    assert "$$$n$$$" in source.body and "Negative numbers" in source.body
    assert "Secret Name" not in source.body and "Two integers" not in source.body
    assert source.samples == (("2\n1 2", "3\n4"),)
    assert source.time_limit == "1 second"
    assert source.note == "A small example."


@pytest.mark.parametrize(
    "html",
    [
        "<html>Access denied</html>",
        HTML.replace("Find the sum", '<img src="diagram.png">Find the sum'),
        HTML.replace("<pre>3<br>4</pre>", ""),
    ],
)
def test_incomplete_or_graphical_statement_is_rejected(html):
    with pytest.raises(ChallengeError):
        parse_statement(html)


def test_rendered_math_preserves_tex_once_without_changing_samples():
    rendered = (
        '<span class="MathJax_Preview">n squared preview</span>'
        '<span class="MathJax" id="MathJax-Element-1-Frame">'
        "<nobr>n squared rendered</nobr>"
        '<span class="MJX_Assistive_MathML"><math><mi>n</mi></math></span>'
        '</span><script id="MathJax-Element-1" type="math/tex; mode=display">'
        r"n^2 < 10^5"
        "</script>"
    )
    html = HTML.replace("$$$n$$$", rendered)
    source = parse_statement(html)
    assert source.body.count("$$$n^2 < 10^5$$$") == 1
    assert "Negative numbers are allowed." in source.body
    assert "preview" not in source.body and "rendered" not in source.body
    assert source.samples == (("2\n1 2", "3\n4"),)


def test_math_tex_scripts_preserve_inline_and_display_source():
    html = HTML.replace(
        "$$$n$$$",
        r'<script type="math/tex">a < b & c</script> and '
        r'<script type="math/tex; mode=display">x > 0</script>',
    )
    source = parse_statement(html)
    assert "$$$a < b & c$$$" in source.body
    assert "$$$x > 0$$$" in source.body


@pytest.mark.parametrize(
    "rendered",
    [
        '<span class="MathJax_Preview">only a preview</span>',
        '<script type="math/tex"></script>',
        "<math><mi>x</mi></math>",
    ],
)
def test_math_without_recoverable_tex_is_rejected(rendered):
    with pytest.raises(ChallengeError, match="公式|数学"):
        parse_statement(HTML.replace("$$$n$$$", rendered))


def test_unrelated_rendered_formula_cannot_be_discarded():
    rendered = (
        '<span class="MathJax" id="MathJax-Element-2-Frame">different formula</span>'
        '<script id="MathJax-Element-1" type="math/tex">n</script>'
    )
    with pytest.raises(ChallengeError):
        parse_statement(HTML.replace("$$$n$$$", rendered))


def test_math_in_specifications_and_note_is_preserved():
    html = (
        HTML.replace(
            "Two integers.",
            r'Two integers, with <script type="math/tex">n \ge 1</script>.',
        )
        .replace(
            "The sum.",
            r'<script type="math/tex">a < b</script>',
        )
        .replace(
            "A small example.",
            r'<script type="math/tex; mode=display">x &lt; y</script>',
        )
    )
    source = parse_statement(html)
    assert "$$$n \\ge 1$$$" in source.input_spec
    assert "$$$a < b$$$" in source.output_spec
    assert "$$$x &lt; y$$$" in source.note


@pytest.mark.parametrize(
    ("markup", "tex"),
    [
        ('<span class="tex-span">x<sub>i</sub></span>', "x_{i}"),
        ("x<sub>i</sub>", "x_{i}"),
        ("10<sup>9</sup>", "10^{9}"),
        ("<b>x</b><sub>i</sub>", r"\boldsymbol{x}_{i}"),
        ("<i>x</i><sub><i>i</i>+1</sub><sup>2</sup>", "x_{i+1}^{2}"),
        (
            '<span class="tex-span">x<sub>i<sup>2</sup></sub></span>',
            "x_{i^{2}}",
        ),
        ("<span>x<sub>i</sub></span><sub>j</sub>", "{x_{i}}_{j}"),
        (
            '<span class="tex-span">{x<sub>i</sub> | i&lt;n}</span>',
            r"\{x_{i} | i<n\}",
        ),
    ],
)
def test_html_indices_reach_statement_as_protected_tex(markup, tex):
    source = parse_statement(HTML.replace("$$$n$$$", markup))
    assert f"$$${tex}$$$" in source.body
    assert source.samples == (("2\n1 2", "3\n4"),)


def test_html_indices_in_all_fields_leave_plain_identifiers_and_samples_unchanged():
    html = (
        HTML.replace("$$$n$$$", "xi and x<sub>i</sub>")
        .replace("Two integers.", "Up to 10<sup>9</sup>.")
        .replace("The sum.", "Print a<sub>i</sub>.")
        .replace("A small example.", "Check x<sub>i+1</sub>.")
        .replace(
            '<div class="test-example-line">2</div>',
            '<div class="test-example-line">x<sub>i</sub> $$$x_i$$$ &lt;sub&gt;</div>',
        )
    )
    source = parse_statement(html)
    assert "xi and $$$x_{i}$$$" in source.body
    assert "$$$10^{9}$$$" in source.input_spec
    assert "$$$a_{i}$$$" in source.output_spec
    assert "$$$x_{i+1}$$$" in source.note
    assert source.samples == (("xi $$$x_i$$$ <sub>\n1 2", "3\n4"),)


@pytest.mark.parametrize(
    "markup",
    [
        "<span><sub>i</sub></span>",
        "x<sub></sub>",
        "x<sub>i</sub><sub>j</sub>",
        '<span class="tex-span">$$$x$$$<sub>i</sub></span>',
        '<span class="tex-span">x<sub><img src="diagram"></sub></span>',
    ],
)
def test_ambiguous_html_indices_are_not_silently_flattened(markup):
    with pytest.raises(ChallengeError):
        parse_statement(HTML.replace("$$$n$$$", markup))


def test_multiple_sample_pairs_remain_unmodified():
    html = HTML.replace(
        '<div class="output"><pre>3<br>4</pre></div>',
        '<div class="output"><pre>3<br>4</pre></div>'
        '<div class="input"><pre>5<br>6</pre></div>'
        '<div class="output"><pre>11</pre></div>',
    )
    assert parse_statement(html).samples == (("2\n1 2", "3\n4"), ("5\n6", "11"))


def translating_model(data):
    fields = {}
    for name in ("body", "input_spec", "output_spec", "note"):
        text = data[name]
        for original, chinese in (
            ("Given ", "给出 "),
            (" integers, find their sum.", " 个整数，求它们的和。"),
            ("First ", "先输入 "),
            (", then the integers. ", "，再输入这些整数。"),
            ("Print the sum.", "输出总和。"),
        ):
            text = text.replace(original, chinese)
        # 模拟模型惯常的公式改写；占位保护应让它根本接触不到公式原文。
        fields[name] = text.replace("$$$", "$").replace("_{i}", "_i").replace(".", "。")
    return fields


def test_translation_preserves_formulas_without_model_copying_them(source, prepared):
    generator = Generator(
        translating_model, {"translation_ok": True, "reference": "Sum once."}
    )
    result = run(Evaluator(generator).prepare(source))
    assert result == replace(prepared, reference="Sum once.")


@pytest.mark.parametrize(
    "damage", ["missing", "duplicated", "moved", "new_formula", "malformed_extra"]
)
def test_damaged_formula_placeholders_stop_before_audit(source, damage):
    def translate(data):
        fields = translating_model(data)
        token = (
            data["body"]
            .removeprefix("Given ")
            .removesuffix(" integers, find their sum.")
        )
        if damage == "missing":
            fields["body"] = fields["body"].replace(token, "")
        elif damage == "duplicated":
            fields["body"] += token
        elif damage == "moved":
            fields["body"] = fields["body"].replace(token, "")
            fields["output_spec"] += token
        elif damage == "new_formula":
            fields["body"] += "$z$"
        else:
            fields["body"] += token[:-2] + "broken]]"
        return fields

    with pytest.raises(ChallengeError):
        run(Evaluator(Generator(translate)).prepare(source))


@pytest.mark.parametrize(
    "formula",
    ["$$$x_{i}$$$", "$$x_{i}$$", r"$x_i + \$1$", r"\(x_{i}\)", r"\[x_{i}\]"],
)
def test_each_supported_formula_delimiter_and_occurrence_is_restored(source, formula):
    source = replace(
        source, body=f"Use {formula} and {formula}.", input_spec="Read values."
    )
    generator = Generator(
        translating_model, {"translation_ok": True, "reference": "Reference."}
    )
    result = run(Evaluator(generator).prepare(source))
    assert result.body == f"Use {formula} and {formula}。"


def test_formula_mentions_can_be_reordered_within_the_same_field(source):
    source = replace(
        source,
        body="The sum of $$$x_{i}$$$ and $$$y$$$.",
        input_spec="Read values.",
    )

    def translate(data):
        fields = translating_model(data)
        first, second = (
            data["body"].removeprefix("The sum of ").removesuffix(".").split(" and ")
        )
        fields["body"] = f"求 {second} 与 {first} 的和。"
        return fields

    generator = Generator(
        translate, {"translation_ok": True, "reference": "Addition is commutative."}
    )
    result = run(Evaluator(generator).prepare(source))
    assert result.body == "求 $$$y$$$ 与 $$$x_{i}$$$ 的和。"


def test_literal_placeholder_like_text_does_not_collide_with_formulas(source):
    source = replace(source, body="Literal [[CF_MATH_0]]; use $$$x_{i}$$$.")
    generator = Generator(
        translating_model, {"translation_ok": True, "reference": "Reference."}
    )
    result = run(Evaluator(generator).prepare(source))
    assert result.body == "Literal [[CF_MATH_0]]; use $$$x_{i}$$$。"


def test_html_index_loss_in_translation_is_rejected_before_publication():
    source = parse_statement(HTML.replace("$$$n$$$", "x<sub>i</sub>"))
    generator = Generator(
        {
            "body": "求 xi 的总和。",
            "input_spec": "两个整数。",
            "output_spec": "输出总和。",
            "note": "一个示例。",
        }
    )
    with pytest.raises(ChallengeError):
        run(Evaluator(generator).prepare(source))


@pytest.mark.parametrize("ok", [False, "true", None])
def test_audit_must_explicitly_accept(source, ok):
    generator = Generator(
        translating_model, {"translation_ok": ok, "reference": "Sum once."}
    )
    with pytest.raises(ChallengeError, match="校对"):
        run(Evaluator(generator).prepare(source))


def test_two_independent_acceptances_needed(source, prepared):
    history = [
        {"role": "user", "content": "我从左到右累加。"},
        {"role": "assistant", "content": "请继续说明。"},
    ]
    generator = Generator(verdict(), verdict())
    result = run(
        Evaluator(generator).judge(
            source, prepared, "Read all and add once.", history=history
        )
    )
    assert result.verdict == "accepted" and len(generator.calls) == 2
    assert generator.calls[0][1] == generator.calls[1][1]
    assert generator.calls[0][1]["conversation_history"] == history
    assert "participant_solution" in generator.calls[1][1]
    assert "first_review" not in generator.calls[1][1]


def test_disagreement_is_needs_more(source, prepared):
    generator = Generator(verdict(), verdict("incorrect", counterexample="n=1"))
    result = run(Evaluator(generator).judge(source, prepared, "My approach"))
    assert result.verdict == "needs_more"


def test_wrong_without_concrete_basis_is_needs_more(source, prepared):
    generator = Generator(verdict("incorrect", complete=True))
    result = run(Evaluator(generator).judge(source, prepared, "My approach"))
    assert result.verdict == "needs_more" and len(generator.calls) == 1
    assert result.mode == "check" and result.complete is True


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "other"},
        {"mode": "hint", "verdict": "accepted"},
        {"mode": "explain", "verdict": "accepted"},
        {"mode": "check", "complete": "true"},
        {"complete": False},
        {"mode": "hint", "verdict": "needs_more"},
    ],
)
def test_invalid_or_contradictory_scope_never_accepts(source, prepared, changes):
    response = verdict(**changes)
    with pytest.raises(ChallengeError):
        run(Evaluator(Generator(response)).judge(source, prepared, "Anything"))


@pytest.mark.parametrize("field", ["mode", "complete"])
def test_missing_scope_field_is_rejected(source, prepared, field):
    response = verdict()
    del response[field]
    with pytest.raises(ChallengeError):
        run(Evaluator(Generator(response)).judge(source, prepared, "Anything"))


@pytest.mark.parametrize("mode", ["hint", "explain"])
def test_guidance_cannot_settle_a_solution(source, prepared, mode):
    response = verdict(
        "needs_more",
        mode=mode,
        complete=False,
        feedback="先试一个很小的输入。",
    )
    result = run(Evaluator(Generator(response)).judge(source, prepared, ""))
    assert result.verdict == "needs_more" and result.mode == mode


@pytest.mark.parametrize("response", ["not json", [], verdict(proof=""), verdict("OK")])
def test_bad_model_response_never_accepts(source, prepared, response):
    with pytest.raises(ChallengeError):
        run(Evaluator(Generator(response)).judge(source, prepared, "Anything"))


def test_json_fence_supported():
    assert parse_json('```json\n{"valid": true}\n```') == {"valid": True}


def test_only_public_fields_sent_and_failure_does_not_reveal(round_):
    content = "\n".join(statement_nodes(round_))
    assert "PRIVATE_REFERENCE" not in content
    assert "Hidden Identity" not in content and "1000A" not in content
    feedback = Review(
        "needs_more", f"See CF 1000A Hidden Identity {round_.problem.url}", "", ""
    )
    text = review_text(feedback, round_)
    assert (
        "1000A" not in text and "Hidden Identity" not in text and "https://" not in text
    )
    assert "1000A" in review_text(Review(**verdict()), round_)


def test_sample_text_is_not_redacted(round_):
    round_.source = replace(round_.source, samples=(("1000A", "Hidden Identity"),))
    content = "\n".join(statement_nodes(round_))
    assert "输入：\n1000A" in content and "输出：\nHidden Identity" in content
    assert "https://" not in hide_identity(round_.problem.url, round_.problem)


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


class Evaluation:
    def __init__(self, prepared, review=None):
        self.prepared = prepared
        self.review = review or Review(**verdict())
        self.calls = 0

    async def prepare(self, _):
        return self.prepared

    async def judge(self, *_):
        self.calls += 1
        return self.review


async def deliver(_):
    pass


@pytest.mark.parametrize(
    ("result", "recheck"),
    [
        pytest.param("incorrect", False, id="incorrect"),
        pytest.param("needs_more", False, id="ambiguous"),
        pytest.param("incorrect", True, id="recheck-disagrees"),
    ],
)
def test_check_keeps_solution_details_out_of_public_feedback_and_history(
    tmp_path, problem, source, prepared, result, recheck
):
    leaked_feedback = (
        "请澄清：你指的是数组 d 中的负数，还是排序后两两差 d_v-d_u 中的负数？"
        if result == "needs_more"
        else "思路有问题：当前结论不成立。漏掉了负权边，反例 d=[0,1,2,3]，最优值 -7。"
    )
    private_proof = "PRIVATE_PROOF：加入全部负权边，再用相邻差连接。"
    evidence = "PRIVATE_COUNTEREXAMPLE：d=[0,1,2,3]，存在总代价为 -7 的合法构造。"
    response = verdict(
        result,
        feedback=leaked_feedback,
        proof=private_proof,
        counterexample=evidence if result == "incorrect" else "",
    )
    generator = Generator(verdict(), response) if recheck else Generator(response)

    async def scenario():
        store = Store(tmp_path)
        service = Challenges(
            store, Catalog(problem, source), ProblemRange(), cooldown=0
        )
        try:
            started = await service.start("group", Evaluation(prepared), deliver)
            round_, review = await service.submit(
                "group", "alice", "n >= 3 是 -mxd，其他情况是 0", Evaluator(generator)
            )
            assert review.verdict == ("needs_more" if recheck else result)
            assert review.proof == private_proof
            if result == "incorrect" and not recheck:
                assert review.counterexample == evidence
            public = review_text(review, round_)
            saved = store.active("group")
            assert saved.id == started.id
            for content in (public, repr(saved.dialogues)):
                for secret in (
                    "负权边",
                    "d_v-d_u",
                    "d=[0,1,2,3]",
                    "-7",
                    "PRIVATE_PROOF",
                    "PRIVATE_COUNTEREXAMPLE",
                ):
                    assert secret not in content
        finally:
            store.close()

    run(scenario())


def test_restart_keeps_active_round_and_seen_history(tmp_path, round_):
    store = Store(tmp_path)
    store.save(round_)
    store.close()
    reopened = Store(tmp_path)
    assert reopened.active(round_.scope).problem.key == round_.problem.key
    assert reopened.seen(round_.scope) == {"1000A"}
    assert reopened.active("another-group") is None
    reopened.close()


def test_restart_interrupts_unconfirmed_publication(tmp_path, round_):
    store = Store(tmp_path)
    round_.status = "publishing"
    store.save(round_)
    store.close()
    reopened = Store(tmp_path)
    assert reopened.active(round_.scope) is None
    assert not reopened.seen(round_.scope)
    reopened.close()


def test_full_flow_groups_winner_and_no_repeat(tmp_path, problem, source, prepared):
    async def scenario():
        store = Store(tmp_path)
        service = Challenges(store, Catalog(problem, source), ProblemRange())
        evaluator = Evaluation(prepared)
        await service.start("group1", evaluator, deliver)
        await service.start("group2", evaluator, deliver)
        one, review = await service.submit(
            "group1", "alice", "Sum all elements", evaluator
        )
        assert one.status == "solved" and one.solver == "alice"
        assert review.verdict == "accepted"
        assert store.active("group2") is not None
        with pytest.raises(ChallengeError, match="进行中"):
            await service.submit("group1", "bob", "Sum all elements", evaluator)
        with pytest.raises(ChallengeError, match="没有未出过"):
            await service.start("group1", evaluator, deliver)
        assert evaluator.calls == 1
        store.close()

    run(scenario())


@pytest.mark.parametrize("winner", [0, 1, 2])
def test_submissions_queue_in_order_until_first_winner(
    tmp_path, problem, source, prepared, winner
):
    async def scenario():
        class GatedEvaluation(Evaluation):
            def __init__(self, review):
                super().__init__(prepared, review)
                self.entered, self.release = asyncio.Event(), asyncio.Event()

            async def judge(self, *args):
                self.entered.set()
                await self.release.wait()
                return await super().judge(*args)

        store = Store(tmp_path)
        service = Challenges(store, Catalog(problem, source), ProblemRange())
        evaluators = [
            GatedEvaluation(
                Review(
                    **verdict(
                        "accepted"
                        if i == winner
                        else ("needs_more" if i == 0 else "incorrect")
                    )
                )
            )
            for i in range(3)
        ]
        tasks = []

        async def submit(index, started):
            started.set()
            return await service.submit("group", str(index), "Sum", evaluators[index])

        try:
            await service.start("group", Evaluation(prepared), deliver)
            for index in range(3):
                started = asyncio.Event()
                tasks.append(asyncio.create_task(submit(index, started)))
                await started.wait()
            assert evaluators[0].entered.is_set()
            assert not any(e.entered.is_set() for e in evaluators[1:])
            with pytest.raises(ChallengeError):
                await service.reveal("group")

            # 另一群无需等待本群队列。
            await service.start("other", Evaluation(prepared), deliver)
            other, _ = await service.submit(
                "other", "outsider", "Sum", Evaluation(prepared)
            )
            assert other.status == "solved"
            for index in range(winner + 1):
                await evaluators[index].entered.wait()
                assert not any(e.entered.is_set() for e in evaluators[index + 1 :])
                evaluators[index].release.set()
                round_, review = await tasks[index]
                if index < winner:
                    assert review.verdict != "accepted"
                    assert store.active("group").id == round_.id
                else:
                    assert round_.status == "solved" and round_.solver == str(winner)
                    assert store.active("group") is None
            for index in range(winner + 1, 3):
                with pytest.raises(ChallengeError):
                    await tasks[index]
                assert not evaluators[index].entered.is_set()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            store.close()

    run(asyncio.wait_for(scenario(), 5))


@pytest.mark.parametrize("outcome", ["error", "cancel"])
def test_queue_continues_after_failure_and_waiter_cancellation(
    tmp_path, problem, source, prepared, outcome
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class InterruptedEvaluation(Evaluation):
            async def judge(self, *_):
                entered.set()
                await release.wait()
                raise RuntimeError("model unavailable")

        store = Store(tmp_path)
        service = Challenges(store, Catalog(problem, source), ProblemRange())
        skipped = Evaluation(prepared)
        surviving = Evaluation(prepared, Review(**verdict("needs_more")))
        tasks = []

        async def submit(sender, evaluator, started):
            started.set()
            return await service.submit("group", sender, "Sum", evaluator)

        try:
            await service.start("group", Evaluation(prepared), deliver)
            first = asyncio.create_task(
                service.submit("group", "first", "Sum", InterruptedEvaluation(prepared))
            )
            tasks.append(first)
            await entered.wait()
            for sender, evaluator in (("cancelled", skipped), ("last", surviving)):
                started = asyncio.Event()
                tasks.append(asyncio.create_task(submit(sender, evaluator, started)))
                await started.wait()
            tasks[1].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[1]
            if outcome == "cancel":
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
            else:
                release.set()
                with pytest.raises(RuntimeError):
                    await first
            round_, review = await tasks[2]
            assert review.verdict == "needs_more"
            assert skipped.calls == 0 and surviving.calls == 1
            assert set(round_.attempts) == {"first", "last"}
            assert store.active("group").id == round_.id
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            store.close()

    run(asyncio.wait_for(scenario(), 5))


def test_queued_solution_cannot_be_judged_against_a_new_round(
    tmp_path, problem, source, prepared
):
    async def scenario():
        store = Store(tmp_path)
        service = Challenges(store, Catalog(problem, source), ProblemRange())
        evaluator = Evaluation(prepared)
        started = asyncio.Event()
        task = None

        async def submit():
            started.set()
            return await service.submit("group", "alice", "Sum", evaluator)

        try:
            old = await service.start("group", Evaluation(prepared), deliver)
            async with service._lock("group"):
                task = asyncio.create_task(submit())
                await started.wait()
                store.save(replace(old, status="revealed"))
                new = replace(old, id="new-round", attempts={})
                store.save(new)
            with pytest.raises(ChallengeError):
                await task
            assert evaluator.calls == 0
            assert store.active("group").id == new.id
            assert store.active("group").attempts == {}
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            store.close()

    run(asyncio.wait_for(scenario(), 5))


def test_failed_delivery_is_not_active_or_seen(tmp_path, problem, source, prepared):
    async def fail(_):
        raise RuntimeError("QQ unavailable")

    async def scenario():
        store = Store(tmp_path)
        service = Challenges(store, Catalog(problem, source), ProblemRange())
        with pytest.raises(RuntimeError):
            await service.start("group", Evaluation(prepared), fail)
        assert store.active("group") is None and not store.seen("group")
        await service.start("group", Evaluation(prepared), deliver)
        assert store.active("group") is not None
        store.close()

    run(scenario())


def test_cooldown_and_uncertain_answer_keeps_round(tmp_path, problem, source, prepared):
    async def scenario():
        store = Store(tmp_path)
        service = Challenges(store, Catalog(problem, source), ProblemRange())
        evaluator = Evaluation(prepared, Review("needs_more", "请补充理由。", "", ""))
        await service.start("group", evaluator, deliver)
        await service.submit("group", "alice", "Sum", evaluator)
        assert store.active("group") is not None
        with pytest.raises(ChallengeError, match="秒"):
            await service.submit("group", "alice", "Sum", evaluator)
        assert evaluator.calls == 1
        store.close()

    run(scenario())
