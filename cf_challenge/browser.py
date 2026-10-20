"""按需通过普通 Chromium 获取 Codeforces 题面 HTML。"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from .models import ChallengeError

_TOTAL_TIMEOUT = 60
_NAVIGATION_TIMEOUT_MS = 30_000
_STATEMENT_TIMEOUT_MS = 25_000
_MATHJAX_TIMEOUT_MS = 10_000


async def fetch_html(url: str) -> str:
    """使用临时、未登录的 Chromium 页面获取实际题面 HTML。"""
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ChallengeError(
            "浏览器兜底依赖未安装，请安装 requirements.txt 并执行 "
            "python -m playwright install chromium；也可启用 local_only 使用本地题库。"
        ) from exc

    playwright = None
    browser = None
    try:
        async with asyncio.timeout(_TOTAL_TIMEOUT):
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=True)
            # 沿用实际浏览器的版本与平台，只去掉会触发 CF 验证页的 Headless 标识。
            probe = await browser.new_page()
            native_agent = await probe.evaluate("navigator.userAgent")
            await probe.close()
            context = await browser.new_context(
                locale="en-US",
                user_agent=native_agent.replace("HeadlessChrome/", "Chrome/"),
            )
            page = await context.new_page()
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=_NAVIGATION_TIMEOUT_MS,
            )
            if response is not None:
                status = response.status
                if status == 429:
                    raise ChallengeError(
                        "Codeforces 题面访问被限流（HTTP 429），未继续请求；"
                        "请稍后再试或导入题面后启用 local_only。"
                    )
                if status >= 400:
                    raise ChallengeError(
                        "Codeforces 暂时无法访问题面；"
                        "请稍后再试或导入题面后启用 local_only。"
                    )

            await page.wait_for_selector(
                ".problem-statement",
                state="attached",
                timeout=_STATEMENT_TIMEOUT_MS,
            )
            # 如果页面引用了 MathJax，等待它加载并排空已有的 Hub 队列；
            # 页面未使用 MathJax 时不额外等待。
            await page.wait_for_function(
                """() => !document.querySelector(
                    'script[src*="MathJax"], script[src*="mathjax"]'
                ) || Boolean(window.MathJax && window.MathJax.Hub)""",
                timeout=_MATHJAX_TIMEOUT_MS,
            )
            await page.evaluate(
                """() => {
                    const hub = window.MathJax && window.MathJax.Hub;
                    if (!hub) return Promise.resolve();
                    return new Promise(resolve => hub.Queue(resolve));
                }"""
            )
            html = await page.content()
            if not html or not await page.locator(".problem-statement").count():
                raise ChallengeError(
                    "网页未返回完整题面，可能被网站访问验证拦截；"
                    "请稍后再试或导入题面后启用 local_only。"
                )
            return html
    except ChallengeError:
        raise
    except PlaywrightTimeoutError as exc:
        raise ChallengeError(
            "题面或公式未能在限定时间内加载，可能被网站访问验证拦截；"
            "请稍后再试或导入题面后启用 local_only。"
        ) from exc
    except TimeoutError as exc:
        raise ChallengeError(
            "获取题面超时，可能被网站访问验证拦截；"
            "请稍后再试或导入题面后启用 local_only。"
        ) from exc
    except Exception as exc:
        raise ChallengeError(
            "无法启动 Chromium 或获取题面，请执行 "
            "python -m playwright install chromium；"
            "也可导入题面后启用 local_only。"
        ) from exc
    finally:
        # 清理也有界；不能用清理异常覆盖原始失败或调用方的取消。
        if browser is not None:
            with suppress(Exception):
                async with asyncio.timeout(5):
                    await browser.close()
        if playwright is not None:
            with suppress(Exception):
                async with asyncio.timeout(5):
                    await playwright.stop()
