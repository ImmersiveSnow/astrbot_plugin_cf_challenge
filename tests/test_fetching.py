import asyncio
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from cf_challenge import browser
from cf_challenge.codeforces import Codeforces
from cf_challenge.models import ChallengeError, ProblemRange
from cf_challenge.service import Challenges
from cf_challenge.storage import Store

HTML = """<div class="problem-statement">
<div class="header"><div class="title">Secret source</div>
<div class="time-limit"><span class="property-title">time limit</span>1 second</div>
<div class="memory-limit"><span class="property-title">memory limit</span>64 MB</div>
</div><p>Find the sum of $$$n$$$ integers.</p>
<div class="input-specification">Read the integers.</div>
<div class="output-specification">Print their sum.</div>
<div class="sample-tests"><div class="input"><pre>2\n1 2</pre></div>
<div class="output"><pre>3</pre></div></div></div>"""


def test_blocked_http_uses_complete_browser_statement_and_persists_cache(
    tmp_path, problem, monkeypatch
):
    async def browser_response(_):
        return HTML

    async def forbidden_browser(_):
        pytest.fail("A cached statement must not open the browser")

    def offline(_):
        pytest.fail("A cached statement must not access the network")

    async def scenario():
        client = Codeforces(tmp_path)
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(403))
        )
        monkeypatch.setattr(browser, "fetch_html", browser_response)
        try:
            source = await client.statement(problem)
            assert source.body == "Find the sum of $$$n$$$ integers."
            assert source.samples == (("2\n1 2", "3"),)
            assert source.time_limit == "1 second"
        finally:
            await client.close()

        monkeypatch.setattr(browser, "fetch_html", forbidden_browser)
        restarted = Codeforces(tmp_path, local_only=True)
        await restarted.http.aclose()
        restarted.http = httpx.AsyncClient(transport=httpx.MockTransport(offline))
        try:
            assert await restarted.statement(problem) == source
        finally:
            await restarted.close()

    asyncio.run(scenario())


def test_rate_limit_does_not_switch_to_browser(tmp_path, problem, monkeypatch):
    async def forbidden_browser(_):
        pytest.fail("Rate limiting must not trigger another transport")

    async def scenario():
        client = Codeforces(tmp_path)
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(429))
        )
        monkeypatch.setattr(browser, "fetch_html", forbidden_browser)
        try:
            with pytest.raises(ChallengeError) as error:
                await client.statement(problem)
            assert problem.key not in str(error.value)
            assert problem.name not in str(error.value)
            assert not client.has_local(problem)
        finally:
            await client.close()

    asyncio.run(scenario())


def test_success_status_without_statement_cannot_create_incomplete_cache(
    tmp_path, problem, monkeypatch
):
    async def incomplete_browser(_):
        return '<div class="problem-statement"><p>Missing input and samples.</p></div>'

    async def scenario():
        client = Codeforces(tmp_path)
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, text="<html>Login required</html>")
            )
        )
        monkeypatch.setattr(browser, "fetch_html", incomplete_browser)
        try:
            with pytest.raises(ChallengeError):
                await client.statement(problem)
            assert not client.has_local(problem)
        finally:
            await client.close()

    asyncio.run(scenario())


def test_unsupported_statement_is_not_retried_or_cached(tmp_path, problem, monkeypatch):
    async def forbidden_browser(_):
        pytest.fail("Rendering must not be used to drop unsupported problem content")

    async def scenario():
        client = Codeforces(tmp_path)
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    text=HTML.replace(
                        "Find the sum", '<img src="diagram">Find the sum'
                    ),
                )
            )
        )
        monkeypatch.setattr(browser, "fetch_html", forbidden_browser)
        try:
            with pytest.raises(ChallengeError):
                await client.statement(problem)
            assert not client.has_local(problem)
        finally:
            await client.close()

    asyncio.run(scenario())


def test_concurrent_same_problem_fetches_share_a_valid_cached_statement(
    tmp_path, problem
):
    async def scenario():
        requests = 0

        async def limited_server(_):
            nonlocal requests
            requests += 1
            if requests > 1:
                return httpx.Response(429)
            await asyncio.sleep(0)
            return httpx.Response(200, text=HTML)

        client = Codeforces(tmp_path)
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(limited_server))
        try:
            first, second = await asyncio.gather(
                client.statement(problem), client.statement(problem)
            )
            assert first.samples == (("2\n1 2", "3"),)
            assert second == first
        finally:
            await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("pages", "outcome", "attempts"),
    [
        pytest.param(["image", "ok"], "active", 2, id="image"),
        pytest.param(["svg", "table", "ok"], "active", 3, id="svg-and-table"),
        pytest.param(["tex", "ok"], "active", 2, id="missing-tex"),
        pytest.param(["mismatch", "ok"], "active", 2, id="unmatched-mathjax"),
        pytest.param(["mathml", "ok"], "active", 2, id="unrecoverable-math"),
        pytest.param(["image"], "unpublished", 1, id="single-candidate"),
        pytest.param(["image", "table"], "unpublished", 2, id="pool-exhausted"),
        pytest.param(
            ["image", "table", "svg", "ok"], "unpublished", 3, id="three-attempt-limit"
        ),
        pytest.param(["image", "429", "ok"], "unpublished", 2, id="rate-limited"),
        pytest.param(
            ["image", "network", "ok"], "unpublished", 2, id="network-failure"
        ),
        pytest.param(["image", "incomplete", "ok"], "unpublished", 2, id="incomplete"),
        pytest.param(["image", "ok", "ok"], "prepare", 2, id="translation-failure"),
        pytest.param(["image", "ok", "ok"], "deliver", 2, id="delivery-failure"),
    ],
)
def test_new_problem_reselects_only_unsupported_statements(
    tmp_path, problem, prepared, monkeypatch, pages, outcome, attempts
):
    candidates = [replace(problem, contest_id=1000 + i) for i in range(len(pages))]
    responses = {p.key: page for p, page in zip(candidates, pages, strict=True)}
    attempted = []
    delivered = []
    prepared_sources = []
    unsupported = {
        "image": '<img src="diagram">',
        "svg": "<svg><text>diagram</text></svg>",
        "table": "<table><tr><td>condition</td></tr></table>",
        "tex": '<script type="math/tex"></script>',
        "mismatch": (
            '<span class="MathJax" id="wrong">rendered</span>'
            '<script type="math/tex" id="eq">n</script>'
        ),
        "mathml": "<math><mi>n</mi></math>",
    }

    def respond(request):
        if request.url.path == "/api/problemset.problems":
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "result": {
                        "problems": [
                            {
                                "contestId": p.contest_id,
                                "index": p.index,
                                "name": p.name,
                                "rating": p.rating,
                                "tags": list(p.tags),
                                "type": "PROGRAMMING",
                            }
                            for p in candidates
                        ]
                    },
                },
            )
        key = "".join(request.url.path.split("/")[-2:])
        attempted.append(key)
        page = responses[key]
        if page == "429":
            return httpx.Response(429)
        if page == "network":
            raise httpx.ConnectError("offline", request=request)
        html = (
            '<div class="problem-statement"><p>Missing sections.</p></div>'
            if page == "incomplete"
            else HTML.replace(
                "Find the sum", unsupported.get(page, "") + "Find the sum"
            )
        )
        return httpx.Response(200, text=html)

    async def prepare(source):
        prepared_sources.append(source)
        if outcome == "prepare":
            raise ChallengeError("模型调用失败。")
        return prepared

    async def deliver(round_):
        delivered.append(round_)
        if outcome == "deliver":
            raise ChallengeError("题面发送失败。")

    async def unavailable_browser(_):
        raise ChallengeError("浏览器读取失败。")

    monkeypatch.setattr(browser, "fetch_html", unavailable_browser)
    monkeypatch.setattr("random.SystemRandom.sample", lambda self, seq, k: seq[:k])

    async def scenario():
        client = Codeforces(tmp_path)
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        store = Store(tmp_path)
        service = Challenges(store, client, ProblemRange())
        try:
            if outcome == "active":
                round_ = await service.start(
                    "group", SimpleNamespace(prepare=prepare), deliver
                )
                assert round_.problem == candidates[attempts - 1]
                assert round_.source.samples == (("2\n1 2", "3"),)
                assert delivered == [round_]
                assert store.active("group").id == round_.id
                assert store.seen("group") == {round_.problem.key}
            else:
                with pytest.raises(ChallengeError) as error:
                    await service.start(
                        "group", SimpleNamespace(prepare=prepare), deliver
                    )
                assert store.active("group") is None
                assert not store.seen("group")
                assert problem.name not in str(error.value)
                assert all(p.key not in str(error.value) for p in candidates)
                assert prepared.reference not in str(error.value)
                assert len(delivered) == int(outcome == "deliver")
            assert attempted == [p.key for p in candidates[:attempts]]
            assert len(prepared_sources) == int(outcome != "unpublished")
            assert not client.has_local(problem)
        finally:
            store.close()
            await client.close()

    asyncio.run(scenario())
