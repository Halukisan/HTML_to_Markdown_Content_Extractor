# HTML to Markdown Content Extractor（HTML 转 Markdown 正文提取器）

项目目标
- 从任意网页 HTML 中自动定位并抽取“主要正文”内容，清洗（去噪、移除 header/footer/广告/导航等干扰）、修正资源链接、并导出为 HTML 和 Markdown 两种格式。
- 支持可选的占位符替换（将资源替换为占位符并给出映射），并提供 HTTP API 与本地 Gradio 前端用于调试与人工验证。

重要提示
- 算法已经非常稳健，切勿修改任何一个数字和字符，每一个分值的计算和确定都是大量的测试数据得到的经验。
- 见下方“注意事项”部分，关于 extract_success 等字段的语义和异常情况说明。

主要特性
- 自动正文定位（多特征打分与规则过滤）
- 去除页面级 header/footer、注释、display:none 元素等干扰
- 生成清理后的 HTML、对应 Markdown，以及纯文本
- 支持通过 xpath 强制定位（如果你已有准确 xpath）
- 支持将图片/资源替换为占位符并输出占位符与原资源映射
- 提供 HTTP API（/、/health、/extract）与本地 Gradio 调试界面

目录结构（关键）
- deploy_docker_local/  
  - app/ — 服务端实现（用于本地开发/本地 Docker 部署的代码），核心逻辑位于 `app.py`，包含正文定位、clean、markdown 转换等函数。  
  - zprogress.py — 本地 Gradio 前端与调试工具（页面输入 HTML、URL、可选 xpath；显示带/不带占位符的输出）。  
- deploy_docker/  
  - app/ — 面向 Docker 部署的服务代码（与 deploy_docker_local 相似，便于容器化）。  
- requirements_updated / requirements.txt — 依赖清单（若不存在以仓库内的名称为准）  
- deploy.sh — 一键部署脚本（本地/服务器快速部署脚本）  
- README.md — 当前文档（项目说明与使用示例）  
- LICENSE — MIT 协议（见仓库 LICENSE 文件）

核心文件说明
- deploy_docker_local/app/app.py  
  - 包含 FastAPI 应用实例、数据模型、正文抽取核心函数（如：find_article_container、remove_page_level_header_footer、calculate_text_density、extract_content_to_markdown 等）。  
  - 数据模型（Pydantic）示例：
    - HTMLInput:
      - html_content: string（必填） — 待处理的 HTML 内容
      - url: string — 源页面 URL（用于修复相对链接）
      - need_placeholder: boolean — 是否启用占位符处理（资源替换）
      - xpath: string — 可选 xpath，提供则跳过自动正文定位并直接用 xpath 抽取
    - MarkdownOutput:
      - html_content: string — 提取后的网页 HTML（一般为正文 HTML）
      - status: string — 内部处理状态（如 success / failed）
      - header_content_text: string — 标题 / header 之类的内容文本（若能抽出）
      - cl_content_html: string — 清理后的正文 HTML（不含 header）
      - cl_content_md: string — 清理后的正文 Markdown
      - content_text / cl_content_text: string — 清理后的正文纯文本
      - placeholder_html / placeholder_markdown: string — 带占位符的 HTML/Markdown
      - placeholder_mapping: string — 资源占位符到真实链接的映射（JSON 字符串）
      - extract_success: boolean — 是否成功抽取正文（非常重要，见下面说明）

- deploy_docker_local/app/zprogress.py  
  - 提供 Gradio 前端：输入 URL / HTML / xpath，按钮触发 `process_frontend_content`，输出带占位符与不带占位符���多种格式（HTML / Markdown / 文本 / 映射等）。

API 接口（HTTP）
1. GET /  
   - 描述：返回基本 API 信息和可用端点列表  
   - 示例响应：
     ```json
     {
       "message": "HTML to Markdown Content Extractor API",
       "version": "2.0.0",
       "endpoints": {
         "/extract": "POST - 提取正文",
         "/health": "GET - 健康检查"
       }
     }
     ```

2. GET /health  
   - 描述：健康检查，确认服务存活  
   - 示例响应：
     ```json
     {
       "status": "healthy",
       "timestamp": "2025-12-10T10:30:00.000000"
     }
     ```

3. POST /extract  
   - 描述：提取 HTML 正文并返回 HTML / Markdown / 文本 等结果  
   - 请求体（JSON，示例）：
     ```json
     {
       "html_content": "<html>...</html>",
       "url": "https://example.com/article/123",
       "need_placeholder": true,
       "xpath": ""
     }
     ```
   - 返回（示例结构）：
     ```json
     {
       "html_content": "<div>正文 HTML</div>",
       "status": "success",
       "header_content_text": "文章标题或面包屑",
       "cl_content_html": "<div><p>清理后的正文</p></div>",
       "cl_content_md": "清理后的正文",
       "content_text": "清理后的正文纯文本",
       "extract_success": true,
       "placeholder_html": "<div><img src='{{IMG_1}}'/></div>",
       "placeholder_markdown": "![alt]({{IMG_1}})",
       "placeholder_mapping": "{\"IMG_1\":\"https://example.com/image.jpg\"}"
     }
     ```

字段语义与异常说明（关键）
- extract_success:
  - True：正文抽取成功（优先使用 cl_content_html / cl_content_md / cl_content_text 作为正文结果）。
    - 但此时 header_content_text 仍可能为空（header 抽取失败属于常见情况）。
  - False：正文抽取失败
    - 虽然 cl_content_html / cl_content_md / cl_content_text 可能有值，但请忽略这些值，直接使用原始输入的 html_content 作为正文。
    - 有时 header_content_text 不为空，可能是算法将正文误判为 header（需人工或后处理修正）。
- need_placeholder:
  - True：服务将下载/替换页面资源（图片、视频、链接等）为占位符，并返回 placeholder_mapping（JSON 字符串），mapping 用于把占位符替换回真实资源。
  - False：不做占位符替换，直接返回清理后的 HTML 与 Markdown。

如何在本地运行（开发环境）
1. 克隆仓库并进入目录：
   ```bash
   git clone https://github.com/Halukisan/XpathGet.git
   cd XpathGet
   ```
2. 创建并激活虚拟环境（示例使用 venv）：
   ```bash
   python3 -m venv venv
   . venv/bin/activate
   ```
3. 安装依赖（以仓库内的 requirements 文件为准）：
   ```bash
   pip install -r requirements_updated
   ```
4. 启动服务（示例使用 uvicorn）：
   ```bash
   # 若使用 deploy_docker_local/app/app.py 中的 FastAPI
   uvicorn deploy_docker_local.app.app:app --host 0.0.0.0 --port 8000 --reload
   ```
5. 打开浏览器访问：
   - API 文档（自动生成的 Swagger / Redoc）：http://localhost:8000/docs 或 /redoc
   - Gradio 本地前端（若使用 zprogress 提供的界面，通常在脚本内启动的端口，请参见 zprogress.py 的运行方式）。

通过 curl 调用示例
- 简单 POST 调用（不启用占位符）：
  ```bash
  curl -X POST "http://localhost:8000/extract" \
    -H "Content-Type: application/json" \
    -d '{
      "html_content": "<html>...</html>",
      "url": "https://example.com/article/1",
      "need_placeholder": false,
      "xpath": ""
    }'
  ```
- 使用 xpath 强制提取（跳过自动定位）：
  ```bash
  curl -X POST "http://localhost:8000/extract" \
    -H "Content-Type: application/json" \
    -d '{
      "html_content": "<html>...</html>",
      "url": "https://example.com",
      "need_placeholder": false,
      "xpath": "//div[@id=\"main-article\"]"
    }'
  ```

Gradio 前端（本地调试）
- 启动 zprogress.py 中的界面后，你可以：
  - 在左侧输入 URL 或直接粘贴 HTML
  - （可选）给出 xpath 强制提取
  - 点击“处理”按钮，右侧将显示带占位符 / 不带占位符的 HTML、Markdown、纯文本与占位符映射，便于人工校验和调试

Docker / 部署
- 仓库包含 deploy 脚本与 Docker 目录（`deploy_docker` / `deploy_docker_local`）用于容器化部署，典型步骤：
  1. 编辑配置（若有）
  2. 构建镜像（若仓库提供 Dockerfile）
  3. 使用 deploy.sh 或自定义 docker-compose 部署
- deploy.sh（仓库内）通常会包含启动容器、拷贝配置、设置日志滚动等操作；在生产环境请仔细检查脚本并根据需要调整。

调试建议与常见问题
- 如果 extract_success 为 False：
  - 请首先尝试传入 xpath（如果你能通过浏览器定位到正确的正文节点）以绕过自动定位。
  - 检查输入 HTML 是否为完整页面（有时只传入片段会导致定位失败）。
- 占位符映射（placeholder_mapping）为 JSON 字符串，解析后可将占位符替换为真实资源 URL。
- 页面含有大量 JS 动态渲染内容时，建议先用 headless 浏览器（如 puppeteer / Playwright）将渲染后的 HTML 导出，再传入本服务。

安全与许可
- 许可证：MIT（详见仓库 LICENSE 文件）
- 注意爬取/处理网页时遵守目标网站的 robots 协议与使用条款，并注意版权/隐私合规。

开发者提示（给维护者）
- 主要逻辑散布在 `deploy_docker_local/app/app.py`（正文定位、清洗、转换）与 `deploy_docker_local/app/zprogress.py`（调���界面）。修改算法参数前请确保有充分的回归测试。
- 日志采用 rotating handler（见 app.py 的日志设置），请关注日志文件以定位边缘案例。

贡献
- 欢迎提交 issue 报告 bug 或提出改进建议。对于算法或规则类改动，请附上对比测试样例（原始 HTML + 期望结果）。

联系方式
- 仓库所有者 / 维护者信息见 GitHub 页面：https://github.com/Halukisan/XpathGet

---

快速上手小结
1. 激活 Python 虚拟环境：`. venv/bin/activate`  
2. 安装依赖：`pip install -r requirements_updated`  
3. 启动 API：`uvicorn deploy_docker_local.app.app:app --host 0.0.0.0 --port 8000`  
4. 调试：访问 `/docs` 或启动 Gradio 前端，粘贴 HTML 测试抽取结果
