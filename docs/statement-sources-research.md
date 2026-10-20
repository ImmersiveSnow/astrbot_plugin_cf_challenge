# CF 题面来源与登录态研究

调查日期：2026-10-20。下文保留实现前的公开资料和实测快照；该阶段未修改运行逻辑、未登录账号、未调用模型或向 QQ 发消息。后续已授权实施的结果见文末，最新安装与行为说明以 [README](../README.md) 为准。

## 结论

- 新增实测后的技术优先级：先验证 CF 普通浏览器兜底与公式解析；再考虑镜像和账号会话。阅读 kouhai-bot 后，本次用未登录 Chromium 成功读取 CF4A、CF2114A。当前插件可解析前者，后者因渲染后的公式格式被安全拒绝，尚不能直接发布。详见下文参考项目调查。
- 匿名镜像备选：洛谷 CF 镜像的英文题面数据。本次在插件 Python 环境中匿名请求 CF4A、CF71A、CF2114A，均取得英文正文、输入输出、成对样例和限制字段。不代表整个题库覆盖完整，也未与 CF 网页逐字比对。
- 认证备选：VJudge 人工登录后复用会话。通用客户端具备 Cookie 持久化能力，但本次没有账号授权，未证明 VJudge 登录后的 HTTP 题面访问、跨重启复用或实际有效期。
- 不建议先做密码自动登录、心跳保活或常驻浏览器。CF 本次浏览器成功不依赖账号登录；若另外选择 VJudge，再先验证人工登录会话的普通 HTTP 复用，不把维护登录态作为所有来源的共同前置条件。
- 技术可访问不等于获得转载许可。洛谷协议 4.1 对发布、改写和再发行有限制；用于群聊发布完整题面前，应确认站点及相关权利人的许可或适用授权。不能因为是非商业群聊就假定允许。VJudge 的登录权限同样不能替代内容使用授权。

## 实测证据

### 洛谷

请求方式：本仓库 `.venv/Scripts/python.exe` 中运行异步 `httpx.AsyncClient`，User-Agent 与插件相同，超时 30 秒，相邻题目请求间隔 2.2 秒，不携带预先登录的 Cookie。

| 页面 | HTTP | 匿名 | 英文正文/输入/输出 | 成对样例 | 原始限制字段 |
| --- | --- | --- | --- | --- | --- |
| [CF4A](https://www.luogu.com.cn/problem/CF4A) | 200 | 是 | 均存在 | 1 组：`8\n` → `YES\n` | time=[1000], memory=[64000] |
| [CF71A](https://www.luogu.com.cn/problem/CF71A) | 200 | 是 | 均存在 | 1 组，包含 4 个单词及对应缩写 | time=[1000], memory=[256000] |
| [CF2114A](https://www.luogu.com.cn/problem/CF2114A) | 200 | 是 | 均存在 | 1 组，包含 5 个用例及对应输出 | time=[1000], memory=[256000] |

验证脚本退出码为 0，检查了题号一致、英文 locale、正文/输入/输出非空、样例成对且非空、限制数组为正整数；CF4A 还核对了样例具体内容。脚本未保存到仓库。

页面包含 `script#lentille-context[type='application/json']`：

- `data.problem.pid`：如 `CF4A`。
- `data.problem.content`：本次三题均为 `locale=en`，含 `description`、`formatI`、`formatO`、`hint`，CF4A 还包含 `background`。
- `data.problem.contenu`：当前展示语言内容，与英文 `content` 不能混用。
- `data.problem.samples`：原始样例字符串，保留换行。
- `data.problem.limits`：时间、内存数组；本次只确认字段存在，未核实单位转换约定与多语言限制映射。
- `data.problem.vjudge.link`：对应 CF 原题链接。

普通网页正文提取会遗漏这些页面的样例和限制，因此不能只读取可见文章文字。页面内嵌 JSON 是本次观察到的实现细节，不是已经确认有兼容承诺的官方题面 API。

接入前仍须核实：多组样例、复杂公式、图片/表格、背景与说明等附加内容、较新题目覆盖、限制单位，以及镜像内容是否滞后或缺失。不能仅凭非空字段就证明语义完整；不支持的内容仍须停止发布。

[洛谷 robots.txt](https://www.luogu.com.cn/robots.txt) 本次列出 `/record`、`/recordnew`、`/paste` 为禁止路径，没有列出 `/problem`。这不是转载许可，也不是批量抓取授权。

[洛谷用户协议](https://help.luogu.com.cn/ula/luogu) 的重要边界：

- 2.2、6.1、6.2：不承诺服务准确、完整、连续可用。
- 3.4.9、3.4.10：不得干扰正常运行或占用、浪费服务器资源。
- 4.1：未经同意不得在媒体发布、为发布而改写或再发行相关内容，仅可作为私人和非商业用途使用。

[洛谷开放平台官方说明](https://docs.lgapi.cn/open/) 虽然提供标准化题库 API，但明确写明题库“不含 RemoteJudge”。因此不能把该开放平台当成已确认可购买或接入的 CF 题面 API；若需授权，应向平台单独确认 CF 镜像是否在可许可范围。

### VJudge

先前实际请求 [CodeForces-4A](https://vjudge.net/problem/CodeForces-4A)、[CodeForces-71A](https://vjudge.net/problem/CodeForces-71A)，均为 303 跳转到带 `login=1` 的首页，最终 200 不包含题面。普通 Chromium 访问 4A 同样显示登录窗口。现有题面解析器正确拒绝实际响应。

本次检查站点首页引用的 12 个公开前端 bundle，未提交登录。[当前登录 bundle](https://vjudge.net/static/bundle/b80c3c6840f77246524d.js) 中观察到：

- 登录发往 `POST /user/login`。
- 请求数据包含 `username`、`password`、`token`。
- 这些只能证明当前前端实现，不能证明密码自动登录长期稳定、token 可省略或存在官方对外认证 API。

[官方 FAQ](https://vjudge.net/article/2771) 未给出可据以承诺的会话有效期、续期接口或长期登录保证。本次也未找到正式公开的题面 API 兼容承诺。

[VJudge robots.txt](https://vjudge.net/robots.txt) 本次只禁止 `/user/`。不能由此推导登录接口、内容再发布或批量请求获准。

未知：登录 Cookie 的实际属性、服务端空闲/绝对超时、是否绑定网络或客户端、是否支持普通 HTTP 读取登录后题面，以及重启后是否仍有效。必须经用户人工登录后再验证。

### Hydro

[实测示例](https://hydro.ac/p/codeforces-P817A) 返回 Cerberus Challenge，而不是题面。页面说明其用于防止滥用流量，需要 JavaScript，并警告频繁刷新可能封禁 IP。本次没有尝试绕过验证，不把它列为比已实测匿名可读来源更省事的 HTTP 抓取候选。

### CF 官方 API

[官方 Problem 对象](https://codeforces.com/apiHelp/objects#Problem) 列出题号、名称、类型、分值、rating 和 tags，不含完整题面。继续用其做选题元数据，不将其当成题面接口。本次未重复确认用户已经报告失败的 CF 标准题面请求。

## 登录态方案：设计建议，尚未接入

1. 人工在专用浏览器配置中完成登录，密码不写入插件配置或发送给助手。不读取整份日常浏览器资料。
2. 如已获站点许可，先验证限定站点的 Cookie 是否足以供独立 HTTP 客户端访问；使用域名、路径、Secure、有效期等属性，而不是全局拼接 Cookie 请求头。
3. HTTP 客户端按站点隔离，避免将 VJudge 登录凭据发送给 CF 或其他来源；接收站点正常返回的新 Cookie 并保存更新。
4. 会话文件位于 AstrBot 插件数据目录，使用仅服务账号可读的权限，不进入仓库、安装包、日志、模型上下文或普通备份分享。
5. 明确处理登录重定向、最终 200 的登录页、401/403 和访问验证；不能只按 HTTP 200 判成功。失效后停止在线获取并通知管理员人工重新登录，有已验证缓存时优先使用缓存。
6. 正常请求可能影响服务端空闲期限，但不能承诺续期；不做高频心跳，不承诺永久在线。
7. 若 HTTP 复用实际失败而浏览器可行，再评估 Playwright 保存认证状态；这将增加浏览器依赖、部署和安全维护成本，不能用来规避站点明确的访问限制。
8. 任何来源成功获取且确认可完整处理后，保存题面缓存；缺字段、公式/样例丢失或图表无法展示时停止发布，不让模型补写。

通用技术依据：

- [HTTPX Clients](https://www.python-httpx.org/advanced/clients/)：支持跨请求 Cookie 持续保存；不等于自动跨进程落盘。
- [Python http.cookiejar](https://docs.python.org/3/library/http.cookiejar.html)：FileCookieJar 子类可加载、保存 Cookie；默认不保存 session cookie，`ignore_discard` 与过期处理须明确设计。保存客户端 Cookie 不会延长服务端有效期。
- [Playwright Authentication](https://playwright.dev/python/docs/auth)：可保存并复用认证状态，且明确警告状态文件可被用于冒充账号，不应提交到仓库。这是通用能力，不是 VJudge 已验证的支持保证。
- [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#session-expiration)：区分服务端空闲与绝对超时；活跃请求不能保证避免绝对超时。会话令牌在有效期间具有账号访问能力，必须按凭据保护。

## kouhai-bot 参考项目调查

项目：[Nerovix/kouhai-bot](https://github.com/Nerovix/kouhai-bot)。固定版本：[34c1523c62ee7e71d91fa171a38141adbb409faf](https://github.com/Nerovix/kouhai-bot/commit/34c1523c62ee7e71d91fa171a38141adbb409faf)。以下区分静态源码证据与本次独立实测，没有安装或运行该项目。

### 实际抓取策略，不是保存登录态

- [get_scraper / fetch_html_http](https://github.com/Nerovix/kouhai-bot/blob/34c1523c62ee7e71d91fa171a38141adbb409faf/src/kouhai_bot/problems/cf_fetcher.py#L66-L144)：每次创建独立 cloudscraper session，结束后关闭。
- [fetch_html](https://github.com/Nerovix/kouhai-bot/blob/34c1523c62ee7e71d91fa171a38141adbb409faf/src/kouhai_bot/problems/cf_fetcher.py#L239-L276)：支持 auto/http/playwright；auto 先 HTTP，遇 forbidden、部分临时 HTTP 错误、超时、连接错误或内容错误时转浏览器。
- [fetch_html_playwright_async](https://github.com/Nerovix/kouhai-bot/blob/34c1523c62ee7e71d91fa171a38141adbb409faf/src/kouhai_bot/problems/cf_fetcher.py#L155-L216)：启动 headless Chromium，创建普通 new_context，等待页面内容，读取 HTML，最后关闭浏览器。没有加载 storage_state 或持久化浏览器 profile。
- [content_valid](https://github.com/Nerovix/kouhai-bot/blob/34c1523c62ee7e71d91fa171a38141adbb409faf/src/kouhai_bot/problems/cf_fetcher.py#L71-L90)：识别空响应、网站验证页和题解加载占位。它不是正文/输入输出/限制/成对样例的完整性校验，不能替换当前插件的 Statement 验证。
- [依赖声明](https://github.com/Nerovix/kouhai-bot/blob/34c1523c62ee7e71d91fa171a38141adbb409faf/pyproject.toml)：包含 cloudscraper、Playwright、Pillow 等。其依赖安装文档只写 uv sync，没有完整说明单独安装 Chromium 的步骤。

这条核心链路直接访问 CF，没有转到 VJudge 或洛谷，也没有证明需要 CF 账号。

### 普通浏览器独立实测

本次使用工具提供的普通 headless Chromium，没有登录 CF，没有安装 cloudscraper，也没有执行该项目源码或使用其定制 User-Agent。

| 页面 | 浏览器结果 | 当前插件 parse_statement 对实际 HTML 的结果 |
| --- | --- | --- |
| CF4A，locale=en | 正文、输入输出、1 second、64 megabytes、样例 8 → YES 均存在；页面仍显示登录入口 | 成功；另断言了样例、时限、内存，以及正文正重量条件和输入上界 |
| CF2114A，locale=en | 取得题面、输入输出、5 个用例的样例；题面含 22 个 math/tex script 和 44 个 MathJax 类节点 | 安全拒绝：含暂不支持的公式格式，未发布 |

两次本地解析探针均退出 0；第二次探针记录并捕获了预期拒绝，不代表该题已经被插件支持。浏览器页已关闭，HTML 样本保留在会话临时研究附件，不进入插件数据或仓库。

这些证据证明当前工具浏览器能读取两题，不能证明 AstrBot 部署机上 Playwright 已可用，也不证明 kouhai-bot 原始 cloudscraper→Playwright 组合已在本机完整运行。

### MathJax 是接入时不能遗漏的环节

[源码 _MathJaxNormalizer / normalize_mathjax](https://github.com/Nerovix/kouhai-bot/blob/34c1523c62ee7e71d91fa171a38141adbb409faf/src/kouhai_bot/problems/fetcher.py#L364-L475) 明确处理浏览器渲染后的数学公式：去掉可见渲染与辅助 MathML 的重复副本，保留 script 中的原始 TeX。否则直接剥 HTML 标签可能让同一公式出现多份文字。

不过其 [html_to_text](https://github.com/Nerovix/kouhai-bot/blob/34c1523c62ee7e71d91fa171a38141adbb409faf/src/kouhai_bot/problems/fetcher.py#L478-L484) 会合并空白并删除美元分隔符，不能整体套用于本插件的题面字段和样例；本插件要求公式可核对、样例保留原样。应独立实现符合本插件结构的 TeX 归一化，不放宽完整性门槛，不用模型猜公式。

该项目还处理图片元数据与视觉模型，但本次没有调用模型验证图像理解；不建议将这些能力作为解决当前 403 的必需范围。

### 可借鉴与不宜照搬

- 值得借鉴：HTTP 与浏览器传输分离、验证页识别、MathJax 原始 TeX 保留、题面缓存后再准备和发布。
- 不宜直接照搬：把 429 也立即切换到另一传输；限流应遵守站点提示，不借浏览器继续冲击同一站点。
- 当前完整性约束更严格，不能用该项目宽松的内容有效判断代替本插件的字段、样例和不支持内容检查。
- [抓取测试](https://github.com/Nerovix/kouhai-bot/blob/34c1523c62ee7e71d91fa171a38141adbb409faf/tests/test_cf_fetcher.py) 使用 FakeScraper、FakeBrowser 等模拟对象；不能当成线上成功率证据。
- README 写 MIT，但仓库元信息 license=null，调查未见独立 LICENSE 文件。若直接复制源码，应先确认许可文本及署名要求；当前只借鉴设计，没有复制其实现。

## 下一步最小验证

先在 AstrBot 所在环境做普通浏览器获取与 TeX 归一化的完整性验证；只有成功后才考虑接入浏览器兜底。保留现有 CF 元数据选题、评审与来源隐藏边界，不自动扩大为图片 OCR 或多模态评审。

先确定内容使用授权。若选择洛谷，验证若干复杂/多样例题目的完整映射，再改 `cf_challenge/codeforces.py` 及相关测试和使用说明。

若选择 VJudge，先用人工登录会话验证一题的完整获取、客户端重建后的复用、Cookie 更新及登录失效识别，再决定是否值得维护。不能在这些验证前承诺可全自动长期运行。

上述调查阶段仅交付研究笔记；没有真实 QQ/模型联调。

## 后续实施结果

已在当前插件中独立实现异步 HTTP → Playwright Chromium 的题面获取与原子缓存，保留完整性检查和 `local_only`。HTTP 429 不切换传输继续请求；浏览器也拒绝 429。MathJax 仅还原可对应的 TeX 源码，孤立渲染、图片和表格仍停止发布，没有接入视觉模型或账号登录。

插件环境的真实抓取验证：CF4A、CF2114A 均先收到 httpx 403，再由实际安装的 Chromium 成功取得并缓存正文、输入输出、样例和限制。原先保存的 CF2114A 浏览器 HTML 中 22 处 TeX 与归一化后内容逐项一致，样例保持一致。

初始 Playwright 默认 HeadlessChrome User-Agent 在本机停留于验证页；改用实际浏览器平台和版本对应的普通 Chrome 标识后成功。这是本次环境下的结果，不保证所有网络环境或未来站点策略均可用，也不需要 CF 账号。

本地实际 Chromium 场景还验证了正常题面、HTTP 429 拒绝、取消传播与清理，返回后未发现新增浏览器或驱动进程残留。独立测试不需要真实 CF、模型或 QQ；尚未在运行中的 AstrBot / QQ / 模型环境联调。

Git 检查发现仓库已经初始化但尚无提交，补充了运行配置、环境变量、密钥、日志和认证状态的忽略规则，未创建提交或推送远程仓库。
