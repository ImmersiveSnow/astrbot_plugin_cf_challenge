"""CF 元数据、完整题面解析和按需浏览器读取。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, Tag

from . import browser
from .models import ChallengeError, Problem, Statement


def _text(element: Tag | None) -> str:
    if element is None:
        return ""
    for br in element.find_all("br"):
        br.replace_with("\n")
    for line in element.select(".test-example-line"):
        line.insert_after("\n")
    for block in element.select("p, li"):
        block.insert_before("\n")
        block.insert_after("\n")
    return element.get_text().replace("\r", "").strip()


def _limit(element: Tag | None) -> str:
    if element is not None:
        title = element.select_one(".property-title")
        if title:
            title.decompose()
    return _text(element)


def _normalize_math(root: Tag) -> None:
    """保留原始 TeX，仅删除能与源码对应的 MathJax 渲染副本。"""

    def in_samples(element: Tag) -> bool:
        return element.find_parent(class_="sample-tests") is not None

    def previous_tag(element: Tag) -> Tag | None:
        previous = element.previous_sibling
        while isinstance(previous, str) and not previous.strip():
            previous = previous.previous_sibling
        return previous if isinstance(previous, Tag) else None

    for script in root.select("script"):
        if in_samples(script):
            continue
        kind = script.get("type", "").split(";", 1)[0].strip().lower()
        tex = script.string
        if kind != "math/tex" or tex is None or not tex.strip():
            raise ChallengeError("题面中的公式没有可恢复的 TeX 源码，已停止出题。")

        rendered = previous_tag(script)
        if rendered is not None and set(rendered.get("class", ())).intersection(
            {"MathJax", "MathJax_Display"}
        ):
            frames = (
                [rendered]
                if "MathJax" in rendered.get("class", ())
                else rendered.select(".MathJax")
            )
            source_id = script.get("id")
            if (
                not source_id
                or len(frames) != 1
                or frames[0].get("id") != f"{source_id}-Frame"
            ):
                raise ChallengeError("题面含无法对应到 TeX 的数学渲染，已停止出题。")
            rendered.decompose()

        preview = previous_tag(script)
        if preview is not None and "MathJax_Preview" in preview.get("class", ()):
            preview.decompose()
        script.replace_with(f"$$${tex}$$$")

    for element in root.select("math, mjx-container, [class], [id]"):
        if in_samples(element):
            continue
        classes = element.get("class", ())
        if (
            element.name in {"math", "mjx-container"}
            or any(
                "mathjax" in name.lower() or name.lower().startswith(("mjx_", "mjx-"))
                for name in classes
            )
            or str(element.get("id", ""))
            .lower()
            .startswith(("mathjax", "mjx-", "mjx_"))
        ):
            raise ChallengeError("题面含无法还原的数学渲染格式，已停止出题。")


def parse_statement(html: str) -> Statement:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".problem-statement")
    if root is None:
        raise ChallengeError("返回的网页中没有完整题面，可能被网站访问验证拦截。")
    # 样例文本按网站原样抽取；仅解析题面其他区域中的 MathJax。
    sample_root = root.select_one(".sample-tests")
    samples = sample_root.select(".input pre") if sample_root else []
    outputs = sample_root.select(".output pre") if sample_root else []
    _normalize_math(root)
    # 图和表格丢失会改变题意。首版仅支持纯文字题，遇到它们停止出题。
    if root.select("img, svg, table, script, math"):
        raise ChallengeError(
            "这道题含图片、表格或不支持的公式格式，首版暂不能完整展示。"
        )
    inp = root.select_one(".input-specification")
    out = root.select_one(".output-specification")
    time_limit = root.select_one(".time-limit")
    memory_limit = root.select_one(".memory-limit")
    note = root.select_one(".note")
    if len(samples) != len(outputs):
        raise ChallengeError("题面样例的输入输出数量不一致，已停止出题。")
    pairs = [[_text(a), _text(b)] for a, b in zip(samples, outputs, strict=True)]
    for element in (inp, out, note):
        if element is not None:
            title = element.select_one(".section-title")
            if title:
                title.decompose()
    data = {
        "input_spec": _text(inp),
        "output_spec": _text(out),
        "samples": pairs,
        "time_limit": _limit(time_limit),
        "memory_limit": _limit(memory_limit),
        "note": _text(note),
    }
    for selector in (
        ".header",
        ".input-specification",
        ".output-specification",
        ".sample-tests",
        ".note",
    ):
        for element in root.select(selector):
            element.decompose()
    data["body"] = _text(root)
    return Statement.from_dict(data)


class Codeforces:
    def __init__(self, directory: Path, local_only: bool = False):
        self.directory = directory
        self.statements = directory / "statements"
        self.statements.mkdir(parents=True, exist_ok=True)
        self.local_only = local_only
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(30),
            follow_redirects=True,
            headers={"User-Agent": "AstrBot-CF-Challenge/0.1"},
        )
        self._lock = asyncio.Lock()
        self._statement_lock = asyncio.Lock()
        self._last_api = 0.0

    def _catalog(self, data: dict) -> list[Problem]:
        if data.get("status") != "OK" or not isinstance(data.get("result"), dict):
            raise ValueError("invalid CF response")
        entries = data["result"].get("problems")
        if not isinstance(entries, list):
            raise ValueError("missing problems")
        result = [p for entry in entries if (p := Problem.from_api(entry)) is not None]
        if not result:
            raise ValueError("empty CF catalog")
        return result

    async def problems(self) -> list[Problem]:
        async with self._lock:
            path = self.directory / "problemset.json"
            if path.exists() and time.time() - path.stat().st_mtime < 3600:
                try:
                    return self._catalog(json.loads(path.read_text(encoding="utf-8")))
                except (ValueError, KeyError, TypeError):
                    pass
            try:
                # CF 官方要求 API 请求间隔至少两秒，缓存和锁也避免并发拉取。
                await asyncio.sleep(max(0, 2.1 - (time.monotonic() - self._last_api)))
                self._last_api = time.monotonic()
                response = await self.http.get(
                    "https://codeforces.com/api/problemset.problems"
                )
                response.raise_for_status()
                data = response.json()
                result = self._catalog(data)
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                temp.replace(path)
                return result
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                # 断网时可使用已缓存的题库；每道题仍要有完整题面。
                if path.exists():
                    try:
                        return self._catalog(
                            json.loads(path.read_text(encoding="utf-8"))
                        )
                    except (ValueError, KeyError, TypeError):
                        pass
                raise ChallengeError("无法获取 CF 题库，请检查网络后重试。") from exc

    def has_local(self, problem: Problem) -> bool:
        return (self.statements / f"{problem.key}.json").is_file()

    async def statement(self, problem: Problem) -> Statement:
        # 串行获取并在锁内重读缓存，避免多群重复启动浏览器或覆盖同一临时文件。
        async with self._statement_lock:
            path = self.statements / f"{problem.key}.json"
            if path.is_file():
                try:
                    return Statement.from_dict(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                except (ValueError, OSError) as exc:
                    raise ChallengeError(
                        "本地题面文件无法读取，请管理员检查导入文件。"
                    ) from exc
            if self.local_only:
                raise ChallengeError("没有这道题的本地题面，请管理员先导入。")
            source = await self._online_statement(problem)
            try:
                temp = path.with_suffix(".tmp")
                temp.write_text(
                    json.dumps(asdict(source), ensure_ascii=False), encoding="utf-8"
                )
                temp.replace(path)
            except OSError as exc:
                raise ChallengeError(
                    "题面已读取但缓存保存失败，请检查插件数据目录权限后重试；"
                    "当前没有发布新题。"
                ) from exc
            return source

    async def _online_statement(self, problem: Problem) -> Statement:
        try:
            response = await self.http.get(problem.url, params={"locale": "en"})
        except httpx.HTTPError:
            response = None
        if response is not None:
            if response.status_code == 429:
                raise ChallengeError(
                    "CF 题面访问被限流（HTTP 429），未继续请求，请稍后重试；"
                    "当前没有发布新题。"
                )
            if response.status_code != 403 and response.status_code < 500:
                if response.is_error:
                    raise ChallengeError(
                        f"CF 题面读取失败（HTTP {response.status_code}），"
                        "请稍后重试或导入本地题面；当前没有发布新题。"
                    )
                # 验证页可能返回 200；有题面时直接解析，内容不完整不能靠重试放行。
                if (
                    BeautifulSoup(response.text, "html.parser").select_one(
                        ".problem-statement"
                    )
                    is not None
                ):
                    return parse_statement(response.text)
        html = await browser.fetch_html(f"{problem.url}?locale=en")
        return parse_statement(html)

    async def close(self) -> None:
        await self.http.aclose()
