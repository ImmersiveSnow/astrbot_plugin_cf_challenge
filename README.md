# CF 群聊解题挑战

这是一个 AstrBot 插件，按指定条件从 Codeforces 抽题，将中文题面排版为带公式的图片，以 QQ「聊天记录」发送。群友 @机器人 提交文字解法；评审通过后恭喜解题者，并公布原题号、题名和链接。

**图片模式尚待真实 QQ 协议端联调。** 已有实际使用反馈确认文字出题可以收到题面；新版默认使用 HTML、内置 KaTeX 和 Chromium 生成分页 PNG。独立验证覆盖实际图片、公式与样例保留、分页、发送回退和 AstrBot 图片消息序列化，但不代表真实 QQ 图片发送或模型判断准确率。题面获取支持异步 HTTP 与 Chromium 兜底；2026-10-20 本机实测 `4A`、`2114A` 在 HTTP 返回 403 后，通过浏览器成功获取并缓存。仍取不到完整原文时停止出题，不让模型凭记忆补写。

## 安装与接入

需要 Python 3.12+、AstrBot 4.x（不低于 4.5.7）及一个已经配置好的聊天模型。

1. 在 AstrBot 接入 QQ 的 OneBot v11 协议端，例如 NapCat。AstrBot 负责插件、模型和消息处理，NapCat 负责连接 QQ；「聊天记录」使用这个接口发送。连接步骤参考 [AstrBot 官方 OneBot v11 文档](https://docs.astrbot.app/platform/aiocqhttp.html)。
2. 将插件解压到 AstrBot 安装目录下的 `data/plugins/astrbot_plugin_cf_challenge/`，确认 `main.py`、`metadata.yaml` 和 `_conf_schema.json` 直接位于该目录。使用发布包时去掉多余的目录嵌套。
3. 使用 **AstrBot 所在的 Python 环境** 安装本插件 `requirements.txt`，并安装 Playwright 的 Chromium 浏览器，然后在 AstrBot 插件页面重载插件。依赖处理方式见 [官方开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)。
4. 在插件配置中选择模型；`provider_id` 留空时沿用当前群会话的模型。选择能处理完整题面、擅长算法推理并能按要求输出 JSON 的模型。
5. 配置允许使用的群号；`allowed_groups` 留空表示所有群。默认仅 AstrBot 管理员、QQ 群主和群管理员能出题、改范围、提前揭晓。
6. 设置默认选题范围。若当前部署环境也无法读取 CF 题面，先按后文导入题面，再开启 `local_only`。

在插件目录中，用实际启动 AstrBot 的 Python 执行：

```text
python -m pip install -r requirements.txt
python -m playwright install chromium
```

上面的 `python` 必须属于 AstrBot 环境，不是随便一个系统 Python；Windows 已有虚拟环境时，使用对应的 `.venv\Scripts\python.exe`，不要误用全局 PyPy。Linux 若缺少浏览器系统库，可按 [Playwright 官方说明](https://playwright.dev/python/docs/browsers#install-system-dependencies) 使用 `python -m playwright install --with-deps chromium`，其中系统依赖安装需要相应权限。容器里也要在容器内部安装，运行用户必须能读取浏览器。Linux 还应安装可用的中文字体，例如 Noto Sans CJK。

**默认图片排版也需要 Chromium**，即使开启 `local_only` 或使用已缓存题面。只有关闭 `statement_images` 才跳过排版浏览器；在线获取原题面失败时仍可能使用抓取浏览器。

命令需要符合 AstrBot 的唤醒前缀设置。下面假定前缀是 `/`；也可以直接 @机器人 后输入 `cf 帮助`。

## 群里怎么用

```text
管理员：/cf 范围 1200 1800
机器人：已更新，下次出题生效。

管理员：/cf 出题
机器人：[聊天记录：题目描述、输入输出、样例、说明]

群友：@机器人 我的思路是……正确性的理由是……时间复杂度是……
机器人：需要补充：这里还没有解释……

群友：@机器人 [重发完整方案，并补充缺少的理由]
机器人：🎉 恭喜……解法评审通过！
        判断依据：……
        原题：CF ……
        https://codeforces.com/problemset/problem/……
```

| 命令 | 作用 |
| --- | --- |
| `/cf 帮助` | 查看用法 |
| `/cf 出题` | 从当前范围抽一题；已有活动题时不覆盖 |
| `/cf 题面` | 重发正在进行的题 |
| `/cf 范围` | 查看本群选题条件 |
| `/cf 范围 1200 1800` | 只修改难度，保留标签和比赛范围 |
| `/cf 范围 {"tags":["dp"],"min_contest":1500,"max_contest":2200}` | 修改指定条件，其余保留 |
| `@机器人 具体解法` | 提交本轮文字解法 |
| `/cf 提交 具体解法` | 显式提交；不需要再额外 @ |
| `/cf 揭晓` | 提前结束并公布原题 |

完整范围示例：

```text
/cf 范围 {"min_rating":1200,"max_rating":1800,"tags":["dp","binary search"],"exclude_tags":["interactive"],"min_contest":1500,"max_contest":2200}
```

难度和比赛编号的上下界都包含在范围内。`tags` 内的标签必须**全部满足**；`exclude_tags` 中任意一个命中就排除。标签使用 CF 的英文标签，例如 `dp`、`graphs`、`binary search`。比赛编号指 CF 网址中的数字，不是标题里的 Round 编号。缺少难度分、非编程题和 Gym 题不参与默认选题。

每群有各自的范围、活动题和出题历史。同一群出过的题不再重复；题池用完后需要扩大范围。修改范围只影响下一轮。默认每人每 30 秒可以提交一次，每次最多 6000 字。**每次提交独立评审，补充时需要把完整方案一起重发。**

## 题面获取、缓存与离线导入

CF 的 `problemset.problems` 接口只提供题目元数据，不提供完整题面，因此插件还要读取题目网页。接口字段见 [CF 官方文档](https://codeforces.com/apiHelp/objects?locale=en)，题库方法见 [官方接口说明](https://codeforces.com/apiHelp/methods?locale=en&mobile=true)。

获取顺序如下：

1. 优先读取已经缓存或人工导入的完整题面。
2. 未启用 `local_only` 且无本地题面时，先用异步 HTTP 读取英文网页。
3. HTTP 返回 403、服务端错误、网络异常，或返回的 200 页面没有题面时，启动临时 Chromium 读取；使用与实际浏览器版本和平台一致的普通 Chrome User-Agent，不登录账号、不保存登录态。
4. **遇到 HTTP 429 限流立即停止**，不切换浏览器继续请求。浏览器仍被拒绝、验证未通过或超时时同样停止，提示重试或本地导入。
5. 只有正文、输入输出、限制和成对样例通过校验，才原子保存到 `statements/<题号>.json`；后续请求和重启后复用缓存。多群共享同一获取锁，避免同时重复启动浏览器。

HTTP 请求超时为 30 秒，浏览器读取总超时为 60 秒，资源清理另有最多 10 秒上限。没有固定的七秒等待。浏览器渲染后的 MathJax 仅使用可对应的原始 TeX，去除重复渲染；缺失源码或不支持的公式格式不会猜测补全。获取成功不保证所有题型都可展示；图片、表格等限制见后文。

数据目录由 AstrBot 的 `StarTools.get_data_dir` 决定，通常是 AstrBot 工作目录下的：

```text
data/plugin_data/astrbot_plugin_cf_challenge/
  problemset.json          # 题库缓存，正常情况下每小时更新
  challenges.sqlite3       # 本群范围、轮次、出题记录、参考分析与提交时间
  statements/
    4A.json                # 自动缓存或人工导入的完整题面
```

可以在浏览器正常访问原题后保存完整 HTML，再在本插件根目录执行以下命令。`--data-dir` 必须改成**实际运行的 AstrBot** 的插件数据目录，容器部署时注意挂载位置。

```text
python -m tools.import_statement --problem 4A --source saved-problem.html --data-dir /path/to/AstrBot/data/plugin_data/astrbot_plugin_cf_challenge
```

导入工具会检查正文、输入输出、限制和成对样例。已有同名文件时默认拒绝覆盖；明确需要替换时加 `--overwrite`。

也可以把完整原文整理成 UTF-8 JSON，然后用相同命令导入。以下仅说明字段格式，是自行编写的例子，**不对应某道 CF 原题**：

```json
{
  "body": "Given two integers a and b, calculate a+b.",
  "input_spec": "Two integers a and b, each between 1 and 100.",
  "output_spec": "Print their sum.",
  "samples": [["2 3", "5"]],
  "time_limit": "1 second",
  "memory_limit": "256 megabytes",
  "note": ""
}
```

导入后在插件配置中打开 `local_only` 并重载插件。这样只从「同时符合范围、且已缓存或导入完整题面」的题中抽取；不会随机抽到另一道尚无本地题面的题。题库第一次仍需网络读取，之后可以使用已缓存的题库。

## 中文题面与解法评审

出题使用两次模型调用：先忠实翻译，再核对原文与中文是否一致，并生成内部参考分析。公式原文另有程序检查，输入输出样例由程序直接附上，不由模型改写。题面不显示题名、题号、链接、难度和算法标签。

### 图片题面

- `statement_images` 默认开启：将公开题面转为安全 HTML，支持 `$$$...$$$`、`$...$`、`$$...$$`、`\(...\)`、`\[...\]` 公式，再用 Chromium 截图。下标、分数、根式等由 KaTeX 排版；不会自行修正模型已经写错或丢失的数学符号。
- 图像宽度为 1440 像素，长题面按实际文字行和公式位置分页；不按固定字符数截断，也不截掉尾页内容。样例采用等宽字体、保留空格换行，不解析其中的公式或 HTML。
- 开启 `merged_forward` 时，每张图片作为聊天记录中的一条节点，通过 AstrBot 的 `Image.fromBytes` 发送，NapCat 不需要访问插件本地文件路径。关闭它则逐张发图片。
- 图片全部生成后才开始发送；合并转发失败会提示并改为直接逐张发图片。直接图片发送失败会使本轮保持发送失败状态，不接受解法。
- 图片渲染失败时会明确提示，并回退到完整文字题面。将 `statement_images` 关闭，可持续使用原文字模式；修复后重新开启，再用 `/cf 题面` 重发当前题，不需要重新抽题、翻译或调用模型。
- KaTeX **0.19.0** 的脚本、样式及 20 个 WOFF2 字体随插件内置，字体嵌入 CSS，渲染时禁用外部请求，不依赖 CDN。第三方 MIT 许可见 `cf_challenge/assets/katex/LICENSE`。题面内容经过 HTML 转义，样例不会执行脚本；外部 TeX 功能及不支持的公式会触发文字回退，而不是生成错误公式图片。

修改 `statement_images` 或 `merged_forward` 后保存配置并重载插件。更新插件代码不需要删除插件数据目录，已有活动题可继续使用。

### 文字解法评审

群友解法有三种结果：

- **通过**：步骤、关键理由、边界和复杂度得到支持；还要另一次复核同意，才结算并揭晓。
- **有错误**：指出反例或明确的错误推导，不揭晓题号。
- **需要补充**：缺少关键步骤、有歧义、两次评审意见不同或模型无法确认，不结算。

内部参考分析由模型推导，**不是 CF 官方题解**；判断也不能只看提交是否与参考分析相似。不同的正确解法允许通过。两次复核仍然使用所选模型，不能保证消除误判；这里没有运行代码，也没有通过 CF 的正式测试。模型输出格式损坏、缺失必需字段或调用失败时不会算通过。

模型调用使用独立上下文，不带日常聊天记录，不授予工具，也不执行群友提交的代码。未通过的反馈会额外屏蔽原题名、题号和链接；这不能保证识别模型产生的所有间接暗示。

## 已处理的异常与当前限制

- 正在出题或评审时，同群其他结算操作会收到忙碌提示；不同群互不占用这把锁。
- 题面成功发送后才成为活动题；发送失败或重启前未确认送达的轮次不会继续接受解法。网络超时有时意味着 QQ 已收到了消息，因此回退发送或重试可能产生重复的题面消息。
- 图片聊天记录发送失败时改为直接发图片；文字模式的合并转发失败时改发分段文字。原文解析支持原始 TeX 和可恢复的 MathJax 公式；含图片、SVG、表格、孤立 MathML 或无法恢复源码的原题会停止发布，避免漏掉信息。
- `allowed_groups`、默认范围和模型选择等插件面板配置修改后需要重载插件；群内 `/cf 范围` 立即保存。
- 已判定解决后，结果发送仍可能因 QQ 网络故障丢失。当前没有结果补发命令，需要从数据目录查看记录。
- 首版由管理员按命令出题，不包含定时推送、积分榜、代码评测或已做题账号同步。

## 开发与验证

下面是在 Windows PowerShell、源代码目录中的命令：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m tools.build_plugin
```

Linux 中把 `.venv\Scripts\python.exe` 换成 `.venv/bin/python`。自动测试中的模型、QQ 事件和转发通道均为模拟对象；它们验证流程和消息结构，不验证真实模型的数学判断。图片功能另外使用实际 Chromium 验证排版、分页、离线资源和异常路径。发布包包含运行文件及公式资源，不包含测试、虚拟环境、数据库、截图预览或密钥。

建议使用 Git 管理源码、测试、配置定义和文档。`.gitignore` 已排除虚拟环境、运行数据、数据库、安装包、环境变量文件、实际 `config.yaml` / `config.json`、密钥、日志和浏览器认证状态目录；不要把 AstrBot 运行配置或账号凭据提交到仓库。初始化、首次提交和远程推送是不同操作，提交前应检查暂存文件范围。

接入后的验收顺序：管理员设置范围并出题 → 确认聊天记录的题面完整且未显示来源 → 普通群友提交错误解法和不完整解法 → 提交完整正确解法并检查揭晓 → 换群和重载插件检查状态隔离与恢复。实际发送消息和调用模型应在你准备好连接后进行。

实现依据：[AstrBot 插件开发](https://docs.astrbot.app/dev/star/plugin-new.html)、[合并转发接口](https://docs.astrbot.app/dev/star/guides/send-message.html)、[模型调用接口](https://docs.astrbot.app/dev/star/guides/ai.html)、[插件配置](https://docs.astrbot.app/dev/star/guides/plugin-config.html)、[CF API](https://codeforces.com/apiHelp)。
