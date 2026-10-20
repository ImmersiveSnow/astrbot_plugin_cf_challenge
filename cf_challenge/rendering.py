"""将公开题面离线排版为一组 PNG 页面。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from html import escape
from pathlib import Path

from .models import ChallengeError, Round
from .presentation import StatementSection, statement_sections

_TOTAL_TIMEOUT = 60
_CLOSE_TIMEOUT = 5
_ASSETS = Path(__file__).with_name("assets") / "katex"

_PAGE_SCRIPT = r"""
async () => {
  const paper = document.querySelector('#paper');
  renderMathInElement(paper, {
    delimiters: [
      {left: '$$$', right: '$$$', display: false},
      {left: '$$', right: '$$', display: true},
      {left: '\\[', right: '\\]', display: true},
      {left: '\\(', right: '\\)', display: false},
      {left: '$', right: '$', display: false}
    ],
    ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
    throwOnError: true,
    strict: 'error',
    output: 'html',
    trust: () => { throw new Error('external TeX commands are disabled'); },
    maxExpand: 1000,
    maxSize: 20,
    errorCallback: () => { throw new Error('formula rendering failed'); }
  });
  void paper.offsetHeight;
  await document.fonts.ready;

  // Keep each formula together; reduce only over-wide expressions, never crop.
  for (const math of paper.querySelectorAll('.katex')) {
    const container = math.closest('.content');
    const available = container.clientWidth - 4;
    const width = math.getBoundingClientRect().width;
    if (width > available) {
      const size = parseFloat(getComputedStyle(math).fontSize);
      const scaled = size * available / width;
      if (scaled < 15) throw new Error('formula cannot fit readably');
      math.style.fontSize = `${scaled}px`;
    }
  }
  await document.fonts.ready;
  if (paper.scrollWidth > paper.clientWidth + 1) {
    throw new Error('statement exceeds page width');
  }

  const box = paper.getBoundingClientRect();
  const guards = [];
  const add = rect => {
    if (rect.height > 0) {
      guards.push([rect.top - box.top - 2, rect.bottom - box.top + 2]);
    }
  };
  // Text-node ranges expose physical wrapped lines, including long sample rows.
  const walker = document.createTreeWalker(paper, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (!node.textContent.trim() || node.parentElement.closest('.katex')) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    for (const rect of range.getClientRects()) {
      if (rect.width > 0) add(rect);
    }
  }
  // KaTeX fractions and roots can paint outside the inline font box.
  for (const math of paper.querySelectorAll('.katex')) {
    const rects = [math.getBoundingClientRect()];
    for (const child of math.querySelectorAll('.katex-html, .katex-html *')) {
      rects.push(...[...child.getClientRects()].filter(
        rect => rect.width > 0 && rect.height > 0
      ));
    }
    const top = Math.min(...rects.map(rect => rect.top));
    const bottom = Math.max(...rects.map(rect => rect.bottom));
    add({top, bottom, height: bottom - top});
  }
  // Do not leave a heading at the end of a page without its first content line.
  for (const heading of paper.querySelectorAll('section > h2')) {
    const content = heading.parentElement.querySelector('.content');
    const range = document.createRange();
    range.selectNodeContents(content);
    const first = [...range.getClientRects()].find(rect => rect.width > 0);
    const top = heading.getBoundingClientRect().top;
    if (first) add({top, bottom: first.bottom, height: first.bottom - top});
  }
  guards.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const guard of guards) {
    const previous = merged[merged.length - 1];
    if (previous && guard[0] <= previous[1]) {
      previous[1] = Math.max(previous[1], guard[1]);
    } else {
      merged.push([...guard]);
    }
  }
  const height = Math.ceil(box.height);
  const clips = [];
  let start = 0;
  while (start < height) {
    let end = Math.min(start + 1600, height);
    const crossing = merged.find(guard => guard[0] < end && guard[1] > end);
    if (end < height && crossing) end = Math.floor(crossing[0]);
    if (end <= start) throw new Error('indivisible content exceeds page height');
    clips.push({
      x: box.left, y: box.top + start, width: box.width, height: end - start
    });
    start = end;
  }
  return clips;
}
"""


def _document(sections: list[StatementSection]) -> str:
    parts = []
    for index, section in enumerate(sections):
        tag = "pre" if section.preformatted else "div"
        heading = f"<h2>{escape(section.title)}</h2>" if index else ""
        parts.append(
            f"<section>{heading}"
            f'<{tag} class="content">{escape(section.text)}</{tag}></section>'
        )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<style>"
        "html,body{margin:0;padding:0;background:white;color:#17212b;"
        'font-family:"Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif;}'
        "#frame{width:960px;margin:0 auto;}"
        "#paper{box-sizing:border-box;width:960px;margin:0 auto;padding:44px 48px;"
        "background:white;}section{margin:0 0 30px;}"
        "section:last-child{margin-bottom:0;}"
        "h2{font-size:26px;line-height:1.5;margin:0 0 14px;padding-bottom:8px;"
        "border-bottom:2px solid #d8e2ec;overflow-wrap:anywhere;}"
        ".content{margin:0;font-size:24px;line-height:1.8;white-space:pre-wrap;"
        "overflow-wrap:anywhere;word-break:normal;}"
        "pre.content{font-family:Consolas,'Cascadia Mono','Microsoft YaHei',monospace;"
        "font-size:21px;"
        "line-height:1.65;background:#f3f6fa;padding:18px 20px;border-radius:8px;}"
        ".katex{display:inline-block;white-space:nowrap;line-height:1.2;}"
        ".katex-display{white-space:normal;margin:.5em 0!important;}"
        ".katex-display>.katex{white-space:nowrap;}"
        '</style></head><body><div id="frame"><main id="paper">'
        + "".join(parts)
        + "</main></div></body></html>"
    )


async def render_statement(round_: Round) -> list[bytes]:
    """渲染公开题面，任一排版或浏览器错误均交由调用方使用文字回退。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ChallengeError("图片题面渲染组件不可用，请改用文字题面。") from exc

    playwright = None
    browser = None
    try:
        async with asyncio.timeout(_TOTAL_TIMEOUT):
            sections = statement_sections(round_)
            html = _document(sections)
            katex = (_ASSETS / "katex.min.js").read_text(encoding="utf-8")
            auto_render = (_ASSETS / "auto-render.min.js").read_text(encoding="utf-8")
            css = (_ASSETS / "katex.min.css").read_text(encoding="utf-8")
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1000, "height": 1800},
                device_scale_factor=1.5,
                offline=True,
            )
            page = await context.new_page()
            await page.route("**/*", lambda route: route.abort())
            await page.set_content(html, wait_until="domcontentloaded", timeout=15_000)
            await page.add_style_tag(content=css)
            await page.add_script_tag(content=katex)
            await page.add_script_tag(content=auto_render)
            clips = await page.evaluate(_PAGE_SCRIPT)
            if not clips:
                raise ValueError("empty statement")
            images = []
            for index, clip in enumerate(clips):
                # 将切片移入视口并裁去其余内容，避免尾页被静默裁短。
                padding = 32 if index else 0
                await page.evaluate(
                    """({top, height, padding}) => {
                        window.scrollTo(0, 0);
                        const paper = document.querySelector('#paper');
                        const frame = document.querySelector('#frame');
                        frame.style.height = `${height}px`;
                        frame.style.marginTop = `${padding}px`;
                        frame.style.overflow = 'hidden';
                        paper.style.transform = `translateY(-${top}px)`;
                    }""",
                    {"top": clip["y"], "height": clip["height"], "padding": padding},
                )
                image = await page.screenshot(
                    type="png",
                    clip={**clip, "y": 0, "height": clip["height"] + padding * 2},
                    animations="disabled",
                    timeout=15_000,
                )
                images.append(image)
            return images
    except asyncio.CancelledError:
        raise
    except ChallengeError:
        raise
    except Exception as exc:
        raise ChallengeError("题面图片生成失败，请改用文字题面。") from exc
    finally:
        if browser is not None:
            with suppress(Exception):
                async with asyncio.timeout(_CLOSE_TIMEOUT):
                    await browser.close()
        if playwright is not None:
            with suppress(Exception):
                async with asyncio.timeout(_CLOSE_TIMEOUT):
                    await playwright.stop()
