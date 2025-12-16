import os
import re
import requests
import urllib.parse
from urllib.parse import parse_qs, urlencode, urlunparse
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter
import markdownify
from typing import Dict
import logging
import hashlib
import json
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="HTML转Markdown处理器API",
    description="将HTML内容转换为带占位符或不带占位符的Markdown格式",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ProcessRequest(BaseModel):
    html_content: str
    url: str = ""

class ProcessResponse(BaseModel):
    status: str
    # 带占位符的结果
    placeholder_html: str = ""
    placeholder_markdown: str = ""
    placeholder_text: str = ""
    placeholder_mapping: str = ""
    # 不带占位符的结果
    html: str = ""
    markdown: str = ""
    text: str = ""

class CustomMarkdownConverter(MarkdownConverter):

    def __init__(self, **options):

        super().__init__(**options)

    def convert_video(self, el, text, convert_as_inline=False, **kwargs):
        """
        专门处理 <video> 标签
        """
        # 1. 获取属性
        src = el.get('src')
        poster = el.get('poster')

        if not src:
            source_tag = el.find('source')
            if source_tag:
                src = source_tag.get('src')

        if not src:
            return ""

        html_output = f'<video src="{src}" controls="controls" width="100%"'

        if poster:
            html_output += f' poster="{poster}"'

        html_output += '></video>'

        return f'\n{html_output}\n'

    def convert_source(self, el, text, convert_as_inline=False, **kwargs):
        """
        处理 <source> 标签，直接忽略，避免重复输出
        """
        return ""
    def convert_table(self, el, text, convert_as_inline=False, **kwargs):
        """
        重写表格转化逻辑
        el: BeautifulSoup 的表格元素对象
        text: 已经被 markdownify 转化过的内部文本（在这个场景下我们可能不用它，而是用 el）
        """
        el['width'] = '100%'
        el['border'] = '1'
        el['cellspacing'] = '0'
        
        if 'style' in el.attrs:
            del el['style']

        html_output = str(el)

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
        stripped_line = line.strip()
        
        if stripped_line:
            cleaned_lines.append(stripped_line)
            prev_empty = False
        elif not prev_empty:
            cleaned_lines.append('')
            prev_empty = True
            
    while cleaned_lines and not cleaned_lines[0]:
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
    
    return '\n'.join(cleaned_lines)

IGNORED_QUERY_PARAMS: set[str] = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'ref', '_t', 'timestamp', 'v', 'cache_bust'
}
def normalize_url(url: str, ignore_params: set[str] = IGNORED_QUERY_PARAMS) -> str:
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

    def _generate_placeholder(self, url: str, element_type: str = "") -> str:
        """
        相同语义的 URL（即使参数顺序不同）会生成相同占位符。
        """
        normalized_url = normalize_url(url)
        
        url_md5 = hashlib.md5(normalized_url.encode('utf-8')).hexdigest()
        placeholder = url_md5[:24]
        
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
                placeholder = self._generate_placeholder(full_src, "video")
                self.placeholder_mapping[placeholder] = full_src
                video['src'] = f"{{{{{placeholder}}}}}"

            # 替换poster属性
            poster = video.get('poster')
            if poster and self.is_media_url(poster):
                full_poster = process_url(poster)
                placeholder = self._generate_placeholder(full_poster, "image")
                self.placeholder_mapping[placeholder] = full_poster
                video['poster'] = f"{{{{{placeholder}}}}}"

        # 处理source标签
        for source in soup.find_all('source'):
            src = source.get('src')
            if src and self.is_media_url(src):
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src, "video")
                self.placeholder_mapping[placeholder] = full_src
                source['src'] = f"{{{{{placeholder}}}}}"

        # 处理audio标签
        for audio in soup.find_all('audio'):
            src = audio.get('src')
            if src and self.is_media_url(src):
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src, "audio")
                self.placeholder_mapping[placeholder] = full_src
                audio['src'] = f"{{{{{placeholder}}}}}"

        # 处理iframe标签
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src')
            if src and ('player' in src.lower() or 'video' in src.lower() or 'audio' in src.lower()):
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src, "embed")
                self.placeholder_mapping[placeholder] = full_src
                iframe['src'] = f"{{{{{placeholder}}}}}"

        # 处理img标签
        for img in soup.find_all('img'):
            src = img.get('src')
            if src and self.is_media_url(src):
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src, "image")
                self.placeholder_mapping[placeholder] = full_src
                img['src'] = f"{{{{{placeholder}}}}}"

        # 处理a标签（下载链接）
        for a in soup.find_all('a'):
            href = a.get('href')
            if href and self.is_media_url(href):
                full_href = process_url(href)
                placeholder = self._generate_placeholder(full_href, "file")
                self.placeholder_mapping[placeholder] = full_href
                a['href'] = f"{{{{{placeholder}}}}}"

        return str(soup)

def process_content(url_input: str, html_input: str) -> ProcessResponse:
    """
    处理输入的内容并返回结果
    """
    try:
        html_content = html_input
        url = url_input

        if not html_content:
            return ProcessResponse(
                status="HTML内容不能为空",
                placeholder_html="",
                placeholder_markdown="",
                placeholder_text="",
                placeholder_mapping="",
                html="",
                markdown="",
                text=""
            )

        # 处理base_url
        if url:
            base_url = process_base_url(url)
        else:
            base_url = ""

        # 调用API获取结果
        try:
            response = requests.post(
                "http://192.168.182.41:8000/extract",
                json={
                    "html_content": html_content,
                    "url": url
                },
                timeout=30
            )

            if response.status_code != 200:
                logger.error(f"API调用失败: {response.status_code}")
                return ProcessResponse(
                    status=f"API调用失败: {response.status_code}",
                    placeholder_html="",
                    placeholder_markdown="",
                    placeholder_text="",
                    placeholder_mapping="",
                    html="",
                    markdown="",
                    text=""
                )

            result = response.json()

            # 不带占位符的结果
            html_without_holder = result.get("cl_content_html", "")
            md_without_holder = result.get("cl_content_md", "")
            text_without_holder = result.get("cl_content_text", "")

        except requests.exceptions.RequestException as e:
            logger.error(f"API调用出错: {str(e)}")
            return ProcessResponse(
                status=f"API调用出错: {str(e)}",
                placeholder_html="",
                placeholder_markdown="",
                placeholder_text="",
                placeholder_mapping="",
                html="",
                markdown="",
                text=""
            )

        # 处理带占位符的结果
        replacer = URLPlaceholderReplacer()
        # 传入base_url以正确处理相对URL
        html_with_placeholders = replacer.replace_urls_with_placeholders(html_without_holder, base_url)

        # 转换带占位符的Markdown
        converter = CustomMarkdownConverter(
            heading_style="ATX",
            bullets="*",
            strip=['script', 'style']
        )
        md_with_placeholders = converter.convert(html_with_placeholders)
        md_with_placeholders = clean_markdown_content(md_with_placeholders)

        # 生成带占位符的纯文本
        text_with_placeholders = html_to_text(html_with_placeholders)

        # 生成占位符映射关系
        placeholder_mapping = json.dumps(replacer.placeholder_mapping, ensure_ascii=False, indent=2)

        return ProcessResponse(
            status="处理成功",
            placeholder_html=html_with_placeholders,
            placeholder_markdown=md_with_placeholders,
            placeholder_text=text_with_placeholders,
            placeholder_mapping=placeholder_mapping,
            html=html_without_holder,
            markdown=md_without_holder,
            text=text_without_holder
        )

    except Exception as e:
        logger.error(f"处理出错: {str(e)}")
        return ProcessResponse(
            status=f"处理出错: {str(e)}",
            placeholder_html="",
            placeholder_markdown="",
            placeholder_text="",
            placeholder_mapping="",
            html="",
            markdown="",
            text=""
        )

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
    将HTML转换为纯文本，保留占位符
    """
    import re
    
    # 1. 在清理HTML之前，先提取所有占位符
    placeholder_pattern = re.compile(r'\{\{[^{}]+\}\}')
    placeholders = placeholder_pattern.findall(html_content)
    
    # 2. 将占位符替换为临时标记，避免在HTML解析过程中被破坏
    temp_markers = []
    processed_html = html_content  # 使用新变量，避免修改原始引用
    
    for i, placeholder in enumerate(placeholders):
        temp_marker = f"__PLACEHOLDER_{i}__"
        temp_markers.append((temp_marker, placeholder))
        processed_html = processed_html.replace(placeholder, temp_marker, 1)
    
    
    # 3. 使用处理后的HTML创建BeautifulSoup对象
    soup = BeautifulSoup(processed_html, 'html.parser')
    
    # 4. 移除script和style标签
    for script in soup(["script", "style"]):
        script.decompose()
    
    # 5. 处理标签属性中的占位符
    # 遍历所有标签，检查属性值中是否包含占位符（现在已经被替换为临时标记）
    tags_with_placeholders = soup.find_all(lambda tag: any(
        temp_marker in str(tag.attrs.get(attr, '')) 
        for attr in tag.attrs 
        for temp_marker, _ in temp_markers
    ))
    
    
    # 6. 对于包含占位符的标签，在标签后添加占位符文本
    for tag in tags_with_placeholders:
        for attr_name, attr_value in tag.attrs.items():
            if isinstance(attr_value, str):
                for temp_marker, original_placeholder in temp_markers:
                    if temp_marker in attr_value:
                        # 在标签后添加占位符作为文本节点
                        placeholder_text = f" {original_placeholder}"
                        new_text = soup.new_string(placeholder_text)
                        tag.insert_after(new_text)
                        break
    
    # 7. 获取文本内容
    text = soup.get_text()
    
    # 8. 清理多余的空白字符
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = '\n'.join(chunk for chunk in chunks if chunk)
    
    
    # 9. 恢复占位符（如果还有没处理的临时标记）
    for temp_marker, original_placeholder in temp_markers:
        if temp_marker in text:
            text = text.replace(temp_marker, original_placeholder)
            print(f"恢复占位符: {temp_marker} -> {original_placeholder}")
    
    return text

# FastAPI 路由定义
@app.get("/")
async def root():
    """
    根路径，返回API信息
    """
    return {
        "title": "HTML转Markdown处理器API",
        "description": "将HTML内容转换为带占位符或不带占位符的Markdown格式",
        "version": "1.0.0",
        "endpoints": {
            "/": "API信息",
            "/process": "POST - 处理HTML内容",
            "/health": "健康检查"
        }
    }

@app.get("/health")
async def health_check():
    """
    健康检查
    """
    return {"status": "healthy"}

@app.post("/process", response_model=ProcessResponse)
async def process_html_content(request: ProcessRequest = Body(...)):
    """
    处理HTML内容，返回转换后的结果

    Args:
        request: 包含HTML内容和URL的请求体

    Returns:
        ProcessResponse: 处理结果，包含带占位符和不带占位符的各种格式
    """
    return process_content(request.url, request.html_content)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "zprogress:app",
        host="0.0.0.0",
        port=8765,
        reload=True,
        log_level="info"
    )



