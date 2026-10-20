import asyncio

import httpx
import pytest

from cf_challenge import browser
from cf_challenge.codeforces import Codeforces
from cf_challenge.models import ChallengeError

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
