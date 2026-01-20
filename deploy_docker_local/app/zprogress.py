import asyncio
import os
import re
import aiohttp
import aiofiles
import requests
import urllib.parse
from urllib.parse import parse_qs, urlencode, urlunparse
from pathlib import Path
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter
import markdownify
from typing import Dict, List, Optional, Tuple, Set
import logging
import uuid
import json
import gradio as gr
import random
import string
import hashlib

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CustomMarkdownConverter(MarkdownConverter):
    """
    自定义转换器：
    1. 方案 A：通过 keep_tags 保留表格 HTML 结构
    2. 方案 B：重写 convert_video 处理视频标签
    """
    
    def __init__(self, **options):
        # 定义所有需要保留为 HTML 的表格标签
        # table_tags = ['table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col']
        
        # options['keep_tags'] = options.get('keep_tags', []) + table_tags
        
        # 初始化父类
        super().__init__(**options)

    def convert_video(self, el, text, convert_as_inline=False, **kwargs):
        """
        专门处理 <video> 标签
        """
        # 1. 获取属性
        src = el.get('src')
        poster = el.get('poster')

        # 如果 <video> 标签本身没有 src，尝试查找内部的 <source> 标签
        if not src:
            source_tag = el.find('source')
            if source_tag:
                src = source_tag.get('src')

        # 如果依然没有找到视频源，返回空字符串
        if not src:
            return ""

        # 2. 构建期望的 HTML 字符串 (强制添加 controls 和 width)
        html_output = f'<video src="{src}" controls="controls" width="100%"'

        if poster:
            html_output += f' poster="{poster}"'

        html_output += '></video>'

        # 3. 返回处理后的字符串
        return f'\n{html_output}\n'

    def convert_source(self, el, text, convert_as_inline=False, **kwargs):
        """
        处理 <source> 标签，直接忽略，避免重复输出
        """
        return ""
    def convert_table(self, el, text, conversion_args=None,**kwargs):
        """
        重写表格转化逻辑
        el: BeautifulSoup 的表格元素对象
        text: 已经被 markdownify 转化过的内部文本（在这个场景下我们可能不用它，而是用 el）
        """
        # 1. (可选) 像处理视频一样，你可以提取或修改属性
        # 例如：强制所有表格宽度 100%，或者加上边框
        el['width'] = '100%'
        el['border'] = '1'
        el['cellspacing'] = '0'
        
        # 也可以删除不需要的属性，比如 style (防止行内样式干扰)
        if 'style' in el.attrs:
            del el['style']

        # 2. 获取处理后的 HTML 字符串
        # str(el) 会获取包含 table 标签及其内部所有子标签(tr, td...)的完整原始 HTML
        # 注意：这样做，表格内部的文字将保留 HTML 格式（比如内部的 <b> 变不成 **），
        # 这通常是保留表格 HTML 时想要的效果。
        html_output = str(el)

        # 3. 返回带换行的字符串 (Markdown 中块级元素最好前后有换行)
        # 我传过来的html都是清理过的,所以输出的html也是干净的,没有多余属性的

        return f'\n{html_output}\n'

def clean_markdown_content(markdown_content: str) -> str:
    """
    清理Markdown内容
    
    Args:
        markdown_content: 原始Markdown内容
        
    Returns:
        str: 清理后的Markdown内容
    """
    if not markdown_content:
        return ""
    markdown_content = markdown_content.replace('\\n', '\n')

    # 1. 按行分割
    lines = markdown_content.splitlines()
    
    cleaned_lines = []
    prev_empty = False
    
    for line in lines:
        # 2. 去除每一行首尾的空白字符
        stripped_line = line.strip()
        
        if stripped_line:
            # 如果这行真的有内容（不仅仅是空格或换行符）
            cleaned_lines.append(stripped_line)
            prev_empty = False
        elif not prev_empty:
            # 如果这行是空的，且前一行不是空的（即：遇到了新的段落间隔）
            # 我们添加一个空字符串，这样最后 join 时会形成双换行 "\n\n"
            cleaned_lines.append('')
            prev_empty = True
            
    # 3. 移除列表开头和结尾可能残留的空行
    while cleaned_lines and not cleaned_lines[0]:
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
    
    # 4. 用单个换行符连接
    # 原理：['标题', '', '正文'] -> "标题\n\n正文"
    return '\n'.join(cleaned_lines)
class HTMLToMarkdownConverter:
    def __init__(self, output_dir: str = "downloads", base_url: str = ""):
        """
        初始化转换器
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.base_url = base_url
        self.downloaded_files: Dict[str, str] = {}

    

    async def replace_urls_with_local_paths(self, html_content: str, url_to_local_path: Dict[str, str]) -> str:
        """
        将HTML中的远程URL替换为本地路径
        """
        soup = BeautifulSoup(html_content, 'html.parser')

        # 替换图片
        for img in soup.find_all('img'):
            src = img.get('src')
            if src:
                if not src.startswith(('http://', 'https://')) and self.base_url:
                    full_src = urllib.parse.urljoin(self.base_url, src)
                else:
                    full_src = src

                if full_src in url_to_local_path:
                    img['src'] = url_to_local_path[full_src]

        # 替换video
        for video in soup.find_all('video'):
            video_src = video.get('src')
            if video_src:
                if not video_src.startswith(('http://', 'https://')) and self.base_url:
                    full_src = urllib.parse.urljoin(self.base_url, video_src)
                else:
                    full_src = video_src

                if full_src in url_to_local_path:
                    video['src'] = url_to_local_path[full_src]

            poster_src = video.get('poster')
            if poster_src:
                if not poster_src.startswith(('http://', 'https://')) and self.base_url:
                    full_src = urllib.parse.urljoin(self.base_url, poster_src)
                else:
                    full_src = poster_src

                if full_src in url_to_local_path:
                    video['poster'] = url_to_local_path[full_src]

        # 替换source
        for source in soup.find_all('source'):
            src = source.get('src')
            if src:
                if not src.startswith(('http://', 'https://')) and self.base_url:
                    full_src = urllib.parse.urljoin(self.base_url, src)
                else:
                    full_src = src

                if full_src in url_to_local_path:
                    source['src'] = url_to_local_path[full_src]

        # 替换链接
        for a in soup.find_all('a'):
            href = a.get('href')
            if href and self.is_download_link(href):
                if not href.startswith(('http://', 'https://')) and self.base_url:
                    full_href = urllib.parse.urljoin(self.base_url, href)
                else:
                    full_href = href

                if full_href in url_to_local_path:
                    a['href'] = url_to_local_path[full_href]

        # 处理特殊的视频div
        for div in soup.find_all('div'):
            div_class = div.get('class') or []
            div_id = div.get('id') or ""
            div_class_str = " ".join(div_class) if isinstance(div_class, list) else str(div_class)

            if ("video" in div_class_str or "video" in str(div_id)):
                iframe = div.find('iframe')
                if iframe:
                    iframe_src = iframe.get('src')
                    if iframe_src:
                        if not iframe_src.startswith(('http://', 'https://')) and self.base_url:
                            full_src = urllib.parse.urljoin(self.base_url, iframe_src)
                        else:
                            full_src = iframe_src

                        if full_src in url_to_local_path:
                            local_path = url_to_local_path[full_src]
                            video_html = f'<video src="{local_path}" controls></video>'
                            video_soup = BeautifulSoup(video_html, 'html.parser')
                            div.replace_with(video_soup)

        return str(soup)

    def clean_table_html(self, table_html: str) -> str:
        """
        清理表格HTML：保留结构，移除无用的布局样式
        """
        try:
            table_soup = BeautifulSoup(table_html, 'html.parser')

            essential_attributes = {
                'table': [],
                'thead': [],
                'tbody': [],
                'tr': [],
                'th': ['colspan', 'rowspan'],
                'td': ['colspan', 'rowspan']
            }

            def clean_style_attribute(style_value: str) -> str:
                if not style_value:
                    return ""
                
                # 保留的语义化样式
                semantic_keywords = ['font-weight', 'font-style', 'text-decoration']
                # 移除的布局样式
                layout_keywords = ['margin', 'padding', 'text-align', 'text-indent',
                                 'width', 'height', 'float', 'position', 'display']

                style_declarations = style_value.split(';')
                kept_declarations = []

                for declaration in style_declarations:
                    declaration = declaration.strip()
                    if not declaration:
                        continue
                    
                    has_semantic = any(keyword in declaration.lower() for keyword in semantic_keywords)
                    has_layout = any(keyword in declaration.lower() for keyword in layout_keywords)

                    if has_semantic and not has_layout:
                        kept_declarations.append(declaration)

                return '; '.join(kept_declarations)

            def clean_tag(tag):
                if tag.name is None:
                    return

                allowed_attrs = essential_attributes.get(tag.name, [])

                if tag.has_attr('style'):
                    cleaned_style = clean_style_attribute(tag['style'])
                    if cleaned_style.strip():
                        tag['style'] = cleaned_style
                        if 'style' not in allowed_attrs:
                            allowed_attrs.append('style')
                    else:
                        del tag['style']

                attrs_to_remove = [attr for attr in tag.attrs if attr not in allowed_attrs]
                for attr in attrs_to_remove:
                    del tag[attr]

                for child in tag.find_all(recursive=False):
                    clean_tag(child)

            clean_tag(table_soup)
            return str(table_soup)

        except Exception as e:
            logger.warning(f"清理表格HTML时出错: {str(e)}")
            return table_html


    def is_download_link(self, url: str) -> bool:
        """判断是否是下载链接"""
        download_extensions = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.tar', '.gz']
        parsed_url = urllib.parse.urlparse(url)
        if any(url.lower().endswith(ext) for ext in download_extensions):
            return True
        if 'download' in parsed_url.query.lower():
            return True
        return False

    def html_to_markdown(self, processed_html: str) -> str:
        """
        使用自定义转换器将 HTML 转换为 Markdown
        """
        # 实例化自定义转换器
        # 注意：这里不再需要手动传 keep_tags=['table']，因为已经在 CustomMarkdownConverter.__init__ 中处理了
        converter = CustomMarkdownConverter(
            heading_style="ATX",
            bullets="*",
            strip=['script', 'style']
        )

        markdown_content = converter.convert(processed_html)

        # 清理多余空行
        markdown_content = re.sub(r'\n\s*\n\s*\n', '\n\n', markdown_content)

        return markdown_content.strip()

    async def convert_html_to_markdown(self, html_content: str, output_filename: str = "output.md") -> str:
        """
        主要转换流程
        """
        try:
            async with aiofiles.open("test_output.html",'w',encoding='utf-8')as f:
                await f.write(html_content)
        

            # 4. 替换链接
            html_with_local_paths = await self.replace_urls_with_local_paths(html_content, url_to_local_path)

            # 5. 转换为 Markdown
            markdown_content = self.html_to_markdown(html_with_local_paths)

            # 6. 保存
            output_path = Path(output_filename)
            async with aiofiles.open(output_path, 'w', encoding='utf-8') as f:
                await f.write(markdown_content)

            logger.info(f"转换完成: {output_path}")
            return str(output_path)

        except Exception as e:
            logger.error(f"转换过程中发生错误: {str(e)}")
            raise

IGNORED_QUERY_PARAMS: Set[str] = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'ref', '_t', 'timestamp', 'v', 'cache_bust'
}
def normalize_url(url: str, ignore_params: Set[str] = IGNORED_QUERY_PARAMS) -> str:
    """
    对URL进行标准化，用于去重和哈希。

    处理：
    - host 转为小写（DNS 不区分大小写）
    - query 参数按键名排序
    - 过滤掉无意义的跟踪参数（可选）
    - 移除 fragment（#xxx）
    - 保留原始 path（不强制小写，因 OSS/S3 路径通常区分大小写）

    注意：返回的是标准化后的字符串，用于生成哈希；原始 URL 应另存用于实际请求。
    """
    parsed = urllib.parse.urlparse(url)
    
    # 解析 query，保留多值参数（如 ?a=1&a=2）
    query_dict = parse_qs(parsed.query, keep_blank_values=True)
    
    # 可选：移除无意义参数
    if ignore_params:
        query_dict = {k: v for k, v in query_dict.items() if k not in ignore_params}
    
    # 按 key 排序，并展开多值（urlencode 需要 list of (key, value)）
    sorted_items = []
    for key in sorted(query_dict.keys()):
        values = query_dict[key]
        for val in values:
            sorted_items.append((key, val))
    
    # 生成标准化 query string（不二次编码已解码的值）
    normalized_query = urlencode(
        sorted_items,
        doseq=False,
        safe='',
        quote_via=lambda x, *args, **kwargs: x  # 保持原样，避免重复编码
    )
    
    # 重建 URL：scheme + 小写 netloc + 原始 path + params + 标准化 query + 无 fragment
    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc.lower(),
        parsed.path,
        parsed.params,
        normalized_query,
        ''  # 清空 fragment
    ))
    
    return normalized
class URLPlaceholderReplacer:
    """
    独立的URL占位符替换器
    格式：{文件夹前缀}/{8位md5}_{8位哈希}.{扩展名}
    """

    def __init__(self):
        self.placeholder_mapping = {}  # 存储占位符与原始URL的对应关系

    def is_media_url(self, url: str) -> bool:
        """
        判断URL是否为媒体文件URL
        """
        if not url:
            return False

        # 视频文件扩展名
        video_extensions = ['.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv', '.m4v']
        # 音频文件扩展名
        audio_extensions = ['.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a']
        # 文件扩展名
        file_extensions = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                         '.zip', '.rar', '.tar', '.gz', '.7z', '.txt', '.rtf']
        # 图片扩展名
        pic_extensions = ['.jpg','.png','.jpeg','.webp','.svg']

        url_lower = url.lower()

        # 检查文件扩展名
        for ext in video_extensions + audio_extensions + file_extensions + pic_extensions:
            if url_lower.endswith(ext):
                return True

        # 检查URL中是否包含媒体相关的关键词
        media_keywords = ['video', 'audio', 'player', 'stream', 'media', 'download', 'file']
        for keyword in media_keywords:
            if keyword in url_lower:
                return True

        return False


    def _generate_placeholder(self, url: str) -> str:
        """
        相同语义的 URL（即使参数顺序不同）会生成相同占位符。
        使用更长的哈希值以减少冲突可能性
        """
        normalized_url = normalize_url(url)

        url_hash = hashlib.sha256(normalized_url.encode('utf-8')).hexdigest()

        # 使用前32位作为占位符，降低冲突概率
        md5content = url_hash[:32]
        prefix = url_hash[:3]
        placeholder = f"{prefix}/{md5content}"
        self.placeholder_mapping[placeholder] = url

        return placeholder
    def replace_urls_with_placeholders(self, html_content: str, base_url: str = "") -> str:
        """
        将HTML中的媒体文件URL替换为占位符
        """
        soup = BeautifulSoup(html_content, 'html.parser')

        def process_url(url: str) -> str:
            """处理单个URL，返回完整的URL"""
            if not url:
                return ""
            # 如果是相对URL，需要拼接base_url
            if not url.startswith(('http://', 'https://')) and base_url:
                url = urllib.parse.urljoin(base_url, url)
            return url

        # 处理video标签
        for video in soup.find_all('video'):
            # 替换src属性
            src = video.get('src')
            if src and self.is_media_url(src):
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src)
                self.placeholder_mapping[placeholder] = full_src
                video['src'] = f"{{{{{placeholder}}}}}"

            # 替换poster属性
            poster = video.get('poster')
            if poster and self.is_media_url(poster):
                full_poster = process_url(poster)
                placeholder = self._generate_placeholder(full_poster)
                self.placeholder_mapping[placeholder] = full_poster
                video['poster'] = f"{{{{{placeholder}}}}}"

        # 处理source标签
        for source in soup.find_all('source'):
            src = source.get('src')
            if src and self.is_media_url(src):
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src)
                self.placeholder_mapping[placeholder] = full_src
                source['src'] = f"{{{{{placeholder}}}}}"

        # 处理audio标签
        for audio in soup.find_all('audio'):
            src = audio.get('src')
            if src and self.is_media_url(src):
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src)
                self.placeholder_mapping[placeholder] = full_src
                audio['src'] = f"{{{{{placeholder}}}}}"

        # 处理iframe标签
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src')
            if src and ('player' in src.lower() or 'video' in src.lower() or 'audio' in src.lower()):
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src)
                self.placeholder_mapping[placeholder] = full_src
                iframe['src'] = f"{{{{{placeholder}}}}}"

        # 处理img标签
        for img in soup.find_all('img'):
            src = img.get('src')
            if src and self.is_media_url(src):
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src)
                self.placeholder_mapping[placeholder] = full_src
                img['src'] = f"{{{{{placeholder}}}}}"

        # 处理a标签（下载链接）
        for a in soup.find_all('a'):
            href = a.get('href')
            if href and self.is_media_url(href):
                full_href = process_url(href)
                placeholder = self._generate_placeholder(full_href)
                self.placeholder_mapping[placeholder] = full_href
                a['href'] = f"{{{{{placeholder}}}}}"

        return str(soup)

   


def process_base_url(url):
    """
    处理base_url，去除最后一个/后面的内容
    """
    if not url:
        return ""

    try:
        from urllib.parse import urlparse
        url_obj = urlparse(url)
        pathname = url_obj.path

        # 找到最后一个/的位置
        last_slash_index = pathname.rfind('/')
        if last_slash_index > 0:
            url_obj = url_obj._replace(path=pathname[:last_slash_index + 1])
        elif pathname == '/':
            # 根路径保持原样
            pass
        else:
            # 没有/，添加一个
            url_obj = url_obj._replace(path='/')

        return url_obj.geturl()
    except Exception as e:
        logger.error(f"URL处理错误: {e}")
        return url



def html_to_text(html_content: str) -> str:
    """
    将HTML转换为纯文本
    优化内存使用和处理性能
    """
    try:
        # 使用BeautifulSoup解析HTML并移除不需要的标签
        soup = BeautifulSoup(html_content, 'html.parser')

        # 移除script和style标签
        for script in soup(["script", "style"]):
            script.decompose()

        # 获取文本内容
        text = soup.get_text()

        # 清理多余的空白字符
        # 移除每行前后的空白
        lines = (line.strip() for line in text.splitlines())
        # 移除多余空格
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        # 过滤空行并合并
        return '\n'.join(chunk for chunk in chunks if chunk)

    except Exception as e:
        logger.error(f"HTML转文本时出错: {str(e)}")
        # 返回原始HTML的安全版本
        return re.sub(r'<[^>]+>', '', html_content)

def process_frontend_content(url_input, html_json_input):
    """
    前端处理函数
    """
    try:
        html_content = html_json_input
        url = url_input

        # 处理base_url
        base_url = process_base_url(url) if url else ""

        # 调用API获取结果（带占位符和不带占位符）
        html_without_holder = ""
        md_without_holder = ""
        text_without_holder = ""
        html_with_placeholders = ""
        md_with_placeholders = ""
        text_with_placeholders = ""
        placeholder_mapping = ""
        header_content = ""
        content_text = ""

        try:
            response = requests.post(
                "http://api:8201/extract",
                # "http://192.168.182.41:8031/extract",
                json={
                    "html_content": html_content,
                    "url": url,
                    "need_placeholder": True
                },
                timeout=30
            )

            if response.status_code == 200:
                try:
                    result = response.json()

                    # 提取各种响应字段
                    html_content_result = result.get("html_content", "")
                    header_content = result.get("header_content_text", "")
                    html_without_holder = result.get("cl_content_html", "")
                    md_without_holder = result.get("cl_content_md", "")
                    have_content = result.get("extract_success", "")
                    content_text = result.get("content_text", "")

                    # 占位符相关字段
                    html_with_placeholders = result.get("placeholder_html", "")
                    md_with_placeholders = result.get("placeholder_markdown", "")
                    placeholder_mapping = result.get("placeholder_mapping", "")

                    # 生成带占位符的纯文本
                    text_with_placeholders = html_to_text(html_with_placeholders)

                    # 不带占位符的纯文本
                    text_without_holder = result.get("cl_content_text", content_text)

                except ValueError:
                    html_without_holder = f"JSON解析失败: {response.text}"
                    md_without_holder = f"JSON解析失败: {response.text}"
                    text_without_holder = f"JSON解析失败: {response.text}"
            else:
                html_without_holder = f"API调用失败: {response.status_code}"
                md_without_holder = f"API调用失败: {response.status_code}"
                text_without_holder = f"API调用失败: {response.status_code}"

        except Exception as e:
            html_without_holder = f"API调用出错: {str(e)}"
            md_without_holder = f"API调用出错: {str(e)}"
            text_without_holder = f"API调用出错: {str(e)}"

        return ("处理成功",
                html_with_placeholders, md_with_placeholders, text_with_placeholders, placeholder_mapping,
                html_without_holder, md_without_holder, text_without_holder)

    except Exception as e:
        return f"处理出错: {str(e)}", "", "", "", "", "", "",""

def create_simple_gradio_interface():
    """
    创建简单的Gradio界面
    """
    with gr.Blocks(title="HTML处理器", theme=gr.themes.Default()) as interface:
        gr.Markdown("# HTML转Markdown处理器")

        with gr.Row():
            # 左侧输入面板
            with gr.Column(scale=1):
                gr.Markdown("## 输入")

                # 小的URL输入框
                url_input = gr.Textbox(
                    label="URL",
                    placeholder="输入URL",
                    lines=1
                )

                # 大的HTML输入框
                html_input = gr.Textbox(
                    label="HTML内容",
                    placeholder='输入HTML',
                    lines=25
                )

                process_btn = gr.Button("处理", variant="primary", size="lg")

                status = gr.Textbox(label="状态", interactive=False)

            # 右侧输出面板
            with gr.Column(scale=2):
                gr.Markdown("## 输出")

                with gr.Tabs():
                    # 带占位符标签页
                    with gr.TabItem("带占位符"):
                        with gr.Tabs():
                            with gr.TabItem("HTML"):
                                placeholder_html = gr.Code(language="html", lines=20)
                            with gr.TabItem("Markdown"):
                                placeholder_md = gr.Code(language="markdown", lines=20)
                            with gr.TabItem("文本"):
                                placeholder_text = gr.Code(language="markdown", lines=20)
                            with gr.TabItem("映射"):
                                placeholder_map = gr.Code(language="json", lines=20)

                    # 不带占位符标签页
                    with gr.TabItem("不带占位符"):
                        with gr.Tabs():
                            with gr.TabItem("HTML"):
                                no_placeholder_html = gr.Code(language="html", lines=20)
                            with gr.TabItem("Markdown"):
                                no_placeholder_md = gr.Code(language="markdown", lines=20)
                            with gr.TabItem("文本"):
                                no_placeholder_text = gr.Code(language="markdown", lines=20)

        # 绑定处理函数
        process_btn.click(
            fn=process_frontend_content,
            inputs=[url_input, html_input],
            outputs=[
                status,
                placeholder_html, placeholder_md, placeholder_text, placeholder_map,
                no_placeholder_html, no_placeholder_md, no_placeholder_text
            ]
        )


    return interface

def process_html_file(input_html_file: str = "1.html", output_prefix: str = "1", url: str = None):
    """
    处理HTML文件的独立函数（参考示例代码）

    Args:
        input_html_file: 输入HTML文件路径
        output_prefix: 输出文件前缀
        url: 可选的URL，如果不提供则使用默认值
    """
    # 读取HTML内容
    with open(input_html_file, 'r', encoding='utf-8') as f:
        html_content = f.read()

    # 使用提供的URL或默认URL
    if url is None:
        url = "https://www.zjzwfw.gov.cn/zjservice-fe/#/workguide?localInnerCode=144f7bb3-4a56-4838-aba7-9a85da72cced"

    # 调用处理函数
    status, html_with_ph, md_with_ph, text_with_ph, ph_mapping, html_without_ph, md_without_ph, text_without_ph = process_frontend_content(url, html_content)

    # 保存结果文件
    with open(f"{output_prefix}placeholder_html.html", 'w', encoding='utf-8') as f:
        f.write(html_with_ph)
    with open(f"{output_prefix}placeholder_markdown.md", 'w', encoding='utf-8') as f:
        f.write(md_with_ph)
    with open(f"{output_prefix}placeholder_mapping.json", 'w', encoding='utf-8') as f:
        f.write(ph_mapping)
    with open(f"{output_prefix}cl_content_text.txt", 'w', encoding='utf-8') as f:
        f.write(text_without_ph)
    with open(f"{output_prefix}html_content.md", 'w', encoding='utf-8') as f:
        f.write(md_without_ph)
    with open(f"{output_prefix}cl_content_html.html", 'w', encoding='utf-8') as f:
        f.write(html_without_ph)

    print(f"处理完成: {status}")
    print(f"生成的文件:")
    print(f"  - {output_prefix}placeholder_html.html")
    print(f"  - {output_prefix}placeholder_markdown.md")
    print(f"  - {output_prefix}placeholder_mapping.json")
    print(f"  - {output_prefix}cl_content_text.txt")
    print(f"  - {output_prefix}html_content.md")
    print(f"  - {output_prefix}cl_content_html.html")


if __name__ == "__main__":
    import sys

    # 检查命令行参数
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        # 命令行模式
        # 使用方法: python zprogressWebDemo.py --cli <input_file> <output_prefix> <url>
        input_file = sys.argv[2] if len(sys.argv) > 2 else "1.html"
        output_prefix = sys.argv[3] if len(sys.argv) > 3 else "1"
        url = sys.argv[4] if len(sys.argv) > 4 else None
        process_html_file(input_file, output_prefix, url)
    else:
        # 启动Gradio界面
        interface = create_simple_gradio_interface()
        interface.launch(
            server_name="0.0.0.0",
            server_port=7860,
            share=False
        )