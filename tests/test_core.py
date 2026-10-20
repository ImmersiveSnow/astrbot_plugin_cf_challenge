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
        return (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )


def verdict(value="accepted", **changes):
    return {
        "verdict": value,
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


def test_multiple_sample_pairs_remain_unmodified():
    html = HTML.replace(
        '<div class="output"><pre>3<br>4</pre></div>',
        '<div class="output"><pre>3<br>4</pre></div>'
        '<div class="input"><pre>5<br>6</pre></div>'
        '<div class="output"><pre>11</pre></div>',
    )
    assert parse_statement(html).samples == (("2\n1 2", "3\n4"), ("5\n6", "11"))


def test_preparation_keeps_reference_private(source, translated):
    generator = Generator(
        translated, {"translation_ok": True, "reference": "Sum once."}
    )
    result = run(Evaluator(generator).prepare(source))
    assert result.reference == "Sum once."
    assert generator.calls[0][1]["samples"] == [["3\n1 2 3", "6"]]


def test_missing_formula_stops_before_audit(source, translated):
    translated["body"] = "求总和。"
    generator = Generator(translated)
    with pytest.raises(ChallengeError, match="公式"):
        run(Evaluator(generator).prepare(source))
    assert len(generator.calls) == 1


@pytest.mark.parametrize("ok", [False, "true", None])
def test_audit_must_explicitly_accept(source, translated, ok):
    generator = Generator(translated, {"translation_ok": ok, "reference": "Sum once."})
    with pytest.raises(ChallengeError, match="校对"):
        run(Evaluator(generator).prepare(source))


def test_two_independent_acceptances_needed(source, prepared):
    generator = Generator(verdict(), verdict())
    result = run(Evaluator(generator).judge(source, prepared, "Read all and add once."))
    assert result.verdict == "accepted" and len(generator.calls) == 2
    assert generator.calls[0][1] == generator.calls[1][1]
    assert "participant_solution" in generator.calls[1][1]


def test_disagreement_is_needs_more(source, prepared):
    generator = Generator(verdict(), verdict("incorrect", counterexample="n=1"))
    result = run(Evaluator(generator).judge(source, prepared, "My approach"))
    assert result.verdict == "needs_more"


def test_wrong_without_concrete_basis_is_needs_more(source, prepared):
    generator = Generator(verdict("incorrect"))
    result = run(Evaluator(generator).judge(source, prepared, "My approach"))
    assert result.verdict == "needs_more" and len(generator.calls) == 1


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
    text = review_text(feedback, round_, "Alice")
    assert (
        "1000A" not in text and "Hidden Identity" not in text and "https://" not in text
    )
    assert "需要补充" in text
    assert "1000A" in review_text(Review(**verdict()), round_, "Alice")


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


def test_concurrent_submissions_do_not_both_win(tmp_path, problem, source, prepared):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class SlowEvaluation(Evaluation):
            async def judge(self, *_):
                entered.set()
                await release.wait()
                return self.review

        store = Store(tmp_path)
        service = Challenges(store, Catalog(problem, source), ProblemRange())
        evaluator = SlowEvaluation(prepared)
        await service.start("group", evaluator, deliver)
        task = asyncio.create_task(service.submit("group", "alice", "Sum", evaluator))
        await entered.wait()
        try:
            with pytest.raises(ChallengeError, match="正在"):
                await service.submit("group", "bob", "Sum", evaluator)
        finally:
            release.set()
            await task
            store.close()

    run(scenario())


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
