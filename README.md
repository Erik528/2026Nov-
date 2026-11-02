# Ad Atelier · Meta 广告多尺寸重构原型

支持 JPG AI 拆图，也支持 PSD 直接读取现有分层并 resize。

JPG 工作流：上传 JPG → OpenAI 识别元素 → API 移除前景、补全背景 → 从原 JPG 像素提取文字和图案 → API 四周扩图 → 人工审核 → 四种尺寸构图与调整 → 真正分层 PSD。

**实现已具备真实 API 调用路径，没有模拟 API 或演示拆分模式。当前已实测真实图像编辑 API 的基础请求，尚未完成真实广告的 API 端到端验收。** 本地测试素材与真实拆分效果严格分开，详见 [VALIDATION.md](VALIDATION.md)。

## 安装与启动

需要 Python 3.11+（实测 Linux x86_64 / Python 3.12）。无需 Node 构建、Docker、数据库或 GPU。

```bash
cd /workspace/2026Nov-  # 本机请改成自己的仓库路径
bash scripts/install.sh
cp .env.example .env
# 在编辑器中填写 .env 的 AD_OPENAI_API_KEY，切勿提交或在聊天中发送密钥
bash scripts/start.sh
```

在本机浏览器访问 `http://127.0.0.1:8000`。云 onboarding UI 不提供本地端口预览链接。设置 `PORT=8001 bash scripts/start.sh` 可更换端口。

`install.sh` 创建 `.venv` 并按 `requirements.lock` 安装全部锁定依赖，再执行 `pip check`。`requirements.txt` 记录直接依赖。可反复运行安装脚本，不会改动项目文件。已有任务可复用当前 checkout；每个云任务本身已隔离，无需新建 worktree。

环境变量：

| 变量 | 默认值 / 用途 |
| --- | --- |
| `AD_OPENAI_API_KEY` | 必需；官方 OpenAI API 密钥；也兼容已有 `OPENAI_API_KEY` |
| `AD_OPENAI_BASE_URL` | `https://api.openai.com/v1`，必须为 HTTPS |
| `AD_VISION_MODEL` | `gpt-4.1`，用于 JSON 坐标识别 |
| `AD_IMAGE_MODEL` | `gpt-image-2.5-sunburst`，需具有图像编辑权限，账户可能要求组织验证 |
| `AD_DATA_DIR` | 项目 `data/`；本地保存上传、检查点、构图和导出 |

只需访问 `api.openai.com`（安装依赖另需 PyPI 及其下载域名）。云环境已保存 `AD_OPENAI_API_KEY` secret 需求及此 API 域名；请在环境设置中安全填写值、审核保存并发布。草稿保存不表示密钥已注入或环境已发布。配置变化后重启服务，并重试真实请求确认生效。

图像与识别请求会向 OpenAI 发送工作图。正常首次处理有 **1 次视觉识别 + 2 次图像编辑** 付费调用；尺寸切换和导出均为本地计算，不重复调用 API。超时不自动重试，避免重复计费；上游超时仍可能计费。模型权限、额度和实际生成质量须在真实账户验证。

## 使用

1. 上传 JPEG（按文件内容检查格式），短边至少 256 px、长边不超过 6000 px、最多 2400 万像素 / 20 MB。读取 EXIF 并校正方向。
2. 点击“拆分并扩图”。阶段与进度实时更新；识别坐标、干净底图、扩展底图单独保存检查点。失败后“继续处理”复用已完成结果；“重新拆分”在确认后删除该任务旧素材、构图与导出并重新调用付费 API。
3. 检查原图/重叠合成对照，分别预览和下载文字 PNG、图案 PNG、干净底图、扩展底图。**任何 API 结果都进入人工审核，不会自动认定质量合格。** 元素记录可通过 `/api/jobs/{id}/assets/elements.json` 获取。
4. 点击确认后开放四种尺寸。初始构图保持文字/主体原有相对位置与比例，将前景整体放入安全区，并额外留 24 px 视觉边距；这是确定性启发式布局，不是经过设计评估的 AI 排版。
5. 选择文字或图案层，拖拽或设置 X、Y、缩放；调整背景水平、垂直裁切与放大。粉色遮罩表示安全区外，开关不影响实际导出。
6. 文字或图案关键层越界时显示对应层并阻止导出。符合约束后导出 PSD，同时保留 PNG 合成预览、JSON 构图及 PSD 检查报告。不同尺寸分别保存；刷新可恢复上次任务。

## 图像与 PSD 实现

- `app/sizes.json` 是四个画布与安全区的唯一配置源，前后端使用同一份数据。
- 视觉 API 返回文字框、图案轮廓与类别，记录归一化坐标以及原图/工作图像素边界。工作图长边最多 1600 px。
- 背景编辑请求将任意长宽比图像等比放入 1024 方形画布，遮罩按相同变换映射；返回后去除垫边。干净底图与原工作图做差，在识别区域内生成近似遮罩；前景 RGB 来自原始 JPG，**不由 AI 重新生成文字、Logo 或产品**。
- 扩展工作画布为 `2W × 2H`，原工作图居中，四边各增加约 50%（奇数边长按像素取整）。API 编辑完成后强制恢复中心干净底图原像素。**大图中间 PNG 采用工作分辨率，不保证原图的全分辨率细节；四种最终画布始终为准确的目标尺寸。**
- 透明前景是基于差分的二值遮罩及缩放重采样，不是高精度语义分割或专用抠图模型。无需将生成出的文字“抠回来”，但背景变化、低对比度、反射和透明区域可能造成孔洞、背景碎片或边缘光晕。
- PSD 由 `psd-tools` 实际编码，依次为 `Background`、`Graphics`、`Text (raster)`；包含真实透明通道、图层坐标与合成图。文字是**不可直接编辑文案的栅格层**。
- 每次导出重新打开 PSD，检查尺寸、三层顺序、可见性、不透明度、坐标、逐像素 RGBA，并比较重新合成与预览；另用 Pillow 解码 PSD 内嵌合成，检查缩略图/默认合成不为空。**未在 Adobe Photoshop 软件中实际打开验证。**

## 验证

```bash
.venv/bin/python -m pytest -q
# 可选：真实浏览器工程检查，需另外安装 playwright 与 Chromium
# .venv/bin/python -m pip install playwright（或在具有已安装 Playwright 的环境运行）
.venv/bin/python tests/browser_check.py
```

浏览器脚本使用 `/usr/bin/chromium`，并通过服务端 API 上传本地构造测试 JPG；直接准备的图层文件只用于验证编辑器与 PSD，**不代表 API 处理结果或真实广告验收**。测试截图和 PSD 位于忽略的 `data/validation/`。

真实 API 验收（请准备含文字、Logo、主体的真实广告 JPG）：

```bash
.venv/bin/python scripts/validate_real.py /path/to/real-ad.jpg
# 浏览器审核生成素材后，使用上一步打印的任务 ID：
.venv/bin/python scripts/validate_real.py --job JOB_ID --confirm-reviewed
```

该脚本不注入测试图层、不模拟提供商响应；缺少密钥或调用失败会明确失败。第二步会导出四种尺寸并打印读取验证报告。还须用 Photoshop 打开导出的文件，人工核对图层、视觉效果与真实设计质量。

## 已知限制

- **真实广告端到端质量与 Photoshop 互操作验收尚未完成**，不能将本地测试通过等同于产品已满足全部验收标准。
- JPG 文字/图案各为一个合并可移动层；会保存对象坐标，但未提供可靠的逐对象拆层。文字与人物交叠、复杂阴影、发丝、半透明玻璃等可能需要手工修图。
- 遮挡后的图案像素无法从单张 JPG 恢复；本版只补全背景。Logo 或产品边缘也可能随近似遮罩受损。
- 重建平均误差与背景变化比例只能辅助审核；不能自动判断文字残留、拼写、Logo 完整性或补全是否自然。质量存疑时不要确认，重新处理或下载 PNG 手动修正。
- 初始布局保持原有前景关系，极端长宽比可能显得拥挤或空旷；请分别调整两层。安全区按合并层非透明像素的外接矩形保守检测，将图案层全部视为关键内容。
- 后端单进程、最多两个 JPG 拆分任务；服务重启后处理中任务显示中断，可用缓存继续。不支持多 Uvicorn worker、多人权限、自动清理、取消上游调用或生产部署。
- 上传与结果存于本地 `data/`，不进 Git；按需备份或删除任务目录。默认仅绑定本机，不要直接作为公开服务部署。


## HTTP 429 排查（有余额也可能发生）

HTTP 429 不能直接等同于“账号余额不足”。新版读取 API 的 `error.code`、`error.type` 及有限的安全响应头，不展示原始错误正文、密钥或上传内容；失败任务会记录 `failed_stage`、`error_details`。程序不自动重复付费调用。

| 诊断代码 | 含义与行动 |
| --- | --- |
| `insufficient_quota` / `billing_hard_limit_reached` / `billing_not_active` | 当前 API 密钥所属组织或项目的计费/配额限制。检查 API Platform 的组织、项目、Billing 与实际限制；ChatGPT Plus/Pro 订阅额度不能抵扣 API 调用。充值后的到账、失效赠金或项目选择也应核对，不能只看另一个项目的余额。 |
| `rate_limit_exceeded` | 模型 RPM（每分钟请求）、TPM（每分钟 Token）、IPM（每分钟图片）等限制；有余额仍会触发。按 `Retry-After` 或重置时间等待、降低并发，检查 API Limits 中具体模型的限制。若单次输入已大于限制，等待无效，需要降低工作图尺寸或申请相应模型限额。 |
| `unknown` | 上游未给出本工具识别的错误代码；仅凭 429 无法定性。保留 Request ID 并核对 API 控制台。 |

在**与启动服务相同的 Python 环境**中运行：

```powershell
python scripts/check_api.py
python scripts/check_api.py --probe
```

第一条只检查本地配置，第二条向当前视觉模型发送一次极小的真实文字请求（可能产生少量 API 费用），不上传图片、不调用图像编辑。输出仅含配置是否存在、模型、API 主机与脱敏诊断。小请求成功不表示大图片请求或图像模型一定未超限。

`AD_OPENAI_API_KEY` 优先于 `OPENAI_API_KEY`；已有进程环境变量优先于 `.env`。请检查是否实际使用了预期项目的密钥，修改配置后重启 PowerShell 中的服务。成功步骤已缓存，重试使用“继续处理（复用缓存）”，不要为排查 429 点击“重新拆分”。


## 2026-11-02：图像模型停用迁移

OpenAI [官方图像编辑参数文档（客户端源码）](https://github.com/openai/openai-python/blob/main/src/openai/types/image_edit_params.py)注明 `gpt-image-1` 已于 2026-10-23 停用，建议改用 `gpt-image-2.5-sunburst` 或 `gpt-image-2.5-flare`。本项目默认值已更新为前者。旧 `.env` 的显式设置会覆盖代码默认值，**已有安装必须自行更新 `.env`**：

```env
AD_IMAGE_MODEL=gpt-image-2.5-sunburst
```

在启动服务的 PowerShell 按 Ctrl+C 停止服务，再按原命令启动，然后点击“继续处理（复用缓存）”。不修改视觉模型，不删除 `data/`；已保存的 `elements.json` 会复用。若同名模型变量设置在 PowerShell 进程环境中，也要同步更新该值。

已使用纯色图真实复现旧模型请求，返回 HTTP 400、`type=image_generation_user_error`、`code=invalid_value`、`param=model`、`The model 'gpt-image-1' does not exist.`。这解释了旧版显示 `code=unknown; type=unknown` 的原因：旧诊断白名单未覆盖这两个标签。新版保留这些安全标签与参数名，并对模型不可用、组织验证、内容审核分类提示；不输出原始错误正文。

模型列表可能仍保留旧名称；能在 `/models` 中看到模型不等于编辑请求仍可用。不同账户的模型权限仍需实际请求验证。

## PSD 直接导入与 resize（新增）

已有分层设计可直接上传 **PSD**。程序读取图层信息，不再对整张 JPG 进行 AI 拆图。原有 JPG 流程继续可用。

1. 上传 PSD（最多 100 MB，RGB / 8 位，标准 PSD；画布短边 ≥256、长边 ≤6000、最多 2400 万像素）。
2. 自动读取可见顶层图层/图层组，显示每层素材、名称、原始合成与导入重叠合成。隐藏层仅记录元数据并保留在原文件中。
3. 在“PSD 图层角色”确认每层为背景、文字、主体/Logo 或装饰。文字与主体按关键内容检查安全区；装饰允许出血。保存角色后确认进入构图。
4. 选择四种尺寸；使用下拉框选择任意前景图层，独立调整位置和缩放，背景使用统一 cover 裁切。**以上步骤、预览及导出均无需 API 密钥。** 默认维持源设计的前景关系，背景不自动扩图、不生成缺失内容。
5. 可选“AI 建议构图”：使用 `AD_VISION_MODEL`，向真实视觉 API 提交合成预览及提取的图层名称、类别、绝对边界、文字内容和组元数据。模型只建议现有图层的坐标/缩放与背景裁切；程序核对全部图层 ID、目标尺寸并约束关键图层安全区，最终仍需人工检查层间遮挡与视觉质量。
6. 导出动态数量的独立栅格图层，保留原顶层图层/组名称和顺序，并增加底部白色 Canvas 层。背景层分别保留，背景统一裁切；前景层分别移动。每次导出仍执行 PSD 重新读取、逐层 RGBA 和合成检查。

每个 PSD/尺寸的 AI 建议只生成并缓存一次，再次点击会重新应用该缓存。手动调整不触发 API；变更图层角色会使旧构图、AI 建议和导出失效。更改角色后需要重新确认。程序不向模型发送 PSD 二进制文件，而是**本地解析真实图层，再发送元数据与预览图**。

### PSD 第一版范围

- 最多 60 个可见顶层单元，总图层数最多 500，渲染单元总像素最多 6000 万。每个顶层组作为一个整体移动；若希望移动组内某对象，请先在 Photoshop 中整理为单独的顶层图层或组。
- **原生文字、智能对象、矢量、组内层级会渲染为独立栅格层，而不是完整保留其 Photoshop 编辑属性。** 原始 PSD 原样保存为 `source.psd`，可通过 `/api/jobs/{id}/assets/source.psd` 下载；元数据在 `psd-layers.json`。
- 支持渲染库可读取的普通图层、文字、组及已有透明度/蒙版。顶层非普通混合、剪贴或调整层会拒绝导入并提示编组/栅格化，避免错误地拆开依赖关系；图层重叠重建与原合成偏差过大也会拒绝。
- 复杂效果的兼容性取决于 PSD 渲染库。用户自己的设计仍需通过导入对照与 Photoshop 打开审核。未导入隐藏层，不支持 PSB、CMYK、16/32 位或纯扁平 PSD。

可复现检查：

```bash
.venv/bin/python -m pytest -q
# 已有运行中的本地服务、Playwright 和 /usr/bin/chromium 时：
.venv/bin/python tests/browser_psd_check.py
```

已验证：真实二进制 PSD 上传、中文图层名、嵌套组元数据、半透明层、角色修改、四种尺寸导出与重新读取；浏览器逐层拖拽、越界拦截、角色保存、背景裁切与刷新恢复。另从 [psd-tools 官方原生文字测试 PSD](https://github.com/psd-tools/psd-tools/blob/main/tests/psd_files/text.psd) 实际读取 Photoshop 文字内容/位置并验证四尺寸栅格分层导出；已对构造 PSD 实际调用一次 OpenAI 视觉接口，验证读取图层元数据、结构化构图、缓存和导出。未验证用户尚未提供的实际广告 PSD，也未在 Adobe Photoshop 软件中打开文件。
