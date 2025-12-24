import gradio as gr
import requests
import json

def process_frontend_content(url_input, html_json_input):
    """
    调用后端接口并处理返回结果
    """
    try:
        html_content = html_json_input
        url = url_input

        try:
            response = requests.post(
                "http://192.168.182.41:8031/extract",
                json={
                    "html_content": html_content,
                    "url": url,
                    "need_placeholder": True
                },
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                status = result.get("status", "未知状态")

                # 带占位符的结果（need_placeholder=True）
                placeholder_html = result.get("placeholder_html", "")
                placeholder_markdown = result.get("placeholder_markdown", "")
                placeholder_mapping = result.get("placeholder_mapping", "")

                # 不带占位符的结果
                cl_content_html = result.get("cl_content_html", "")
                cl_content_md = result.get("cl_content_md", "")
                content_text = result.get("content_text", "")
            else:
                err_msg = f"API调用失败: {response.status_code}"
                status = err_msg
                placeholder_html = placeholder_markdown = placeholder_mapping = ""
                cl_content_html = cl_content_md = content_text = err_msg
        except Exception as e:
            err_msg = f"API调用出错: {str(e)}"
            status = err_msg
            placeholder_html = placeholder_markdown = placeholder_mapping = ""
            cl_content_html = cl_content_md = content_text = err_msg

        return (
            status,
            placeholder_html, placeholder_markdown, content_text, placeholder_mapping,
            cl_content_html, cl_content_md, content_text
        )

    except Exception as e:
        err_msg = f"处理出错: {str(e)}"
        return (err_msg, "", "", "", "", "", "", "")


def create_simple_gradio_interface():
    """
    创建 Gradio 界面
    """
    with gr.Blocks(title="HTML正文提取与占位符处理", theme=gr.themes.Default()) as interface:
        gr.Markdown("# 🧹 HTML正文提取工具（支持URL占位符）")

        with gr.Row():
            # 左侧输入区
            with gr.Column(scale=1):
                gr.Markdown("## 📥 输入")

                url_input = gr.Textbox(
                    label="原始网页 URL",
                    placeholder="例如：https://example.com/article",
                    lines=1
                )

                html_input = gr.Textbox(
                    label="原始 HTML 内容（含噪音）",
                    placeholder="粘贴包含正文但混杂无关元素的完整 HTML",
                    lines=25
                )

                process_btn = gr.Button("🚀 提取并处理", variant="primary", size="lg")
                status_output = gr.Textbox(label="状态信息", interactive=False)

            # 右侧输出区
            with gr.Column(scale=2):
                gr.Markdown("## 📤 输出结果")

                with gr.Tabs():
                    # 带占位符 Tab
                    with gr.TabItem("🔖 带占位符（用于还原链接）"):
                        with gr.Tabs():
                            with gr.TabItem("HTML"):
                                ph_html = gr.Code(language="html", lines=20)
                            with gr.TabItem("Markdown"):
                                ph_md = gr.Code(language="markdown", lines=20)
                            with gr.TabItem("纯文本"):
                                ph_txt = gr.Textbox(lines=20, interactive=False)
                            with gr.TabItem("🔗 占位符映射 (JSON)"):
                                ph_map = gr.Code(language="json", lines=20)

                    # 不带占位符 Tab
                    with gr.TabItem("🧹 不带占位符（干净内容）"):
                        with gr.Tabs():
                            with gr.TabItem("HTML"):
                                clean_html = gr.Code(language="html", lines=20)
                            with gr.TabItem("Markdown"):
                                clean_md = gr.Code(language="markdown", lines=20)
                            with gr.TabItem("纯文本"):
                                clean_txt = gr.Textbox(lines=20, interactive=False)

        # 绑定事件
        process_btn.click(
            fn=process_frontend_content,
            inputs=[url_input, html_input],
            outputs=[
                status_output,
                ph_html, ph_md, ph_txt, ph_map,
                clean_html, clean_md, clean_txt
            ]
        )

    return interface


if __name__ == "__main__":
    interface = create_simple_gradio_interface()
    interface.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False
    )
