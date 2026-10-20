"""CF 元数据、完整题面解析和按需浏览器读取。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from . import browser
from .models import ChallengeError, Problem, Statement, UnsupportedStatement


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
            raise UnsupportedStatement(
                "题面中的公式没有可恢复的 TeX 源码，已停止出题。"
            )

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
                raise UnsupportedStatement(
                    "题面含无法对应到 TeX 的数学渲染，已停止出题。"
                )
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
            raise UnsupportedStatement("题面含无法还原的数学渲染格式，已停止出题。")


_INDEX_BASE = re.compile(r"(?:[^\W\d]\w*|\d+(?:\.\d+)?|\([^()]+\)|\[[^\[\]]+\])$")
_HTML_MATH_INLINE = {"span", "i", "em", "b", "strong"}
_HTML_MATH_ESCAPES = str.maketrans(
    {
        "{": r"\{",
        "}": r"\}",
        "_": r"\_",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
    }
)


def _html_math_tex(element: Tag) -> str:
    """只转换显式 HTML 数学结构，不把内容当作可执行的 TeX 命令。"""
    parts: list[str] = []
    scripts: set[str] = set()
    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            text = str(child)
            if "$" in text or "\\" in text:
                raise UnsupportedStatement(
                    "题面混合了无法可靠转换的公式格式，已停止出题。"
                )
            parts.append(text.translate(_HTML_MATH_ESCAPES))
            if text.strip():
                scripts.clear()
            continue
        if child.name not in _HTML_MATH_INLINE and child.name not in {"sub", "sup"}:
            raise UnsupportedStatement("题面中的上下标含不支持的结构，已停止出题。")
        tex = _html_math_tex(child).strip()
        if not tex:
            raise UnsupportedStatement("题面中的数学结构为空，已停止出题。")
        if child.name in {"sub", "sup"}:
            if not "".join(parts).strip() or child.name in scripts:
                raise UnsupportedStatement("题面中的上下标无法可靠对应，已停止出题。")
            scripts.add(child.name)
            operator = "_" if child.name == "sub" else "^"
            parts.append(f"{operator}{{{tex}}}")
        else:
            if child.name in {"b", "strong"}:
                tex = r"\boldsymbol{" + tex + "}"
            elif child.select_one("sub, sup") is not None:
                tex = "{" + tex + "}"
            parts.append(tex)
            scripts.clear()
    return "".join(parts)


def _normalize_html_indices(root: Tag) -> None:
    """在丢弃 HTML 标签前，保留上下标与明确的公式基底；样例保持原样。"""
    for formula in root.select(".tex-span"):
        if (
            formula.find_parent(class_="sample-tests") is not None
            or formula.find_parent(class_="tex-span") is not None
            or formula.select_one("sub, sup") is None
        ):
            continue
        formula.replace_with(f"$$${_html_math_tex(formula).strip()}$$$")

    def sibling(node, direction):
        result = getattr(node, direction)
        while isinstance(result, Comment) or (
            isinstance(result, NavigableString) and not result.strip()
        ):
            result = getattr(result, direction)
        return result

    # 从外层的末尾处理，避免先拆掉作为另一个上下标基底的内层公式。
    for index in reversed(root.select("sub, sup")):
        if (
            index.find_parent(class_="sample-tests") is not None
            or index.find_parent(["sub", "sup"]) is not None
            or not any(parent is root for parent in index.parents)
        ):
            continue
        first = index
        base = sibling(first, "previous_sibling")
        while isinstance(base, Tag) and base.name in {"sub", "sup"}:
            first = base
            base = sibling(first, "previous_sibling")
        text = str(base) if isinstance(base, NavigableString) else ""
        match = _INDEX_BASE.search(text.rstrip()) if text else None
        if match is not None:
            prefix = text[: match.start()]
            tex = match.group().translate(_HTML_MATH_ESCAPES)
        elif (
            isinstance(base, Tag)
            and base.name in _HTML_MATH_INLINE
            and _INDEX_BASE.fullmatch(base.get_text().strip())
        ):
            prefix = ""
            tex = _html_math_tex(base).strip()
            if base.name in {"b", "strong"}:
                tex = r"\boldsymbol{" + tex + "}"
            elif base.select_one("sub, sup") is not None:
                tex = "{" + tex + "}"
        else:
            raise UnsupportedStatement("题面中的上下标缺少明确基底，已停止出题。")
        scripts: set[str] = set()
        last = first
        while True:
            content = _html_math_tex(last).strip()
            if not content or last.name in scripts:
                raise UnsupportedStatement("题面中的上下标无法可靠对应，已停止出题。")
            scripts.add(last.name)
            operator = "_" if last.name == "sub" else "^"
            tex += f"{operator}{{{content}}}"
            following = sibling(last, "next_sibling")
            if not isinstance(following, Tag) or following.name not in {"sub", "sup"}:
                break
            last = following
        node, stop = base.next_sibling, last.next_sibling
        base.replace_with(f"{prefix}$$${tex}$$$")
        while node is not None and node is not stop:
            following = node.next_sibling
            node.extract()
            node = following


def parse_statement(html: str) -> Statement:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".problem-statement")
    if root is None:
        raise ChallengeError("返回的网页中没有完整题面，可能被网站访问验证拦截。")
    # 样例按网站原样抽取；其他区域先恢复公式和显式上下标，再提取文字。
    sample_root = root.select_one(".sample-tests")
    samples = sample_root.select(".input pre") if sample_root else []
    outputs = sample_root.select(".output pre") if sample_root else []
    _normalize_math(root)
    _normalize_html_indices(root)
    # 图和表格丢失会改变题意，仅支持文字和可恢复的 TeX，不能丢弃其他内容。
    if root.select("img, svg, table, script, math"):
        raise UnsupportedStatement(
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
