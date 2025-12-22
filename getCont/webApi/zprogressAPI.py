import os
import re
import requests
import urllib.parse
from urllib.parse import parse_qs, urlencode, urlunparse
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter
import markdownify
from typing import Dict, Optional, Set
import logging
import hashlib
import json
from fastapi import FastAPI, HTTPException, Body, Request
from pydantic import BaseModel, field_validator, HttpUrl
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 配置常量
CONFIG = {
    "extract_api_url": os.getenv("EXTRACT_API_URL", "http://192.168.182.41:8000/extract"),
    "max_html_size": int(os.getenv("MAX_HTML_SIZE", 10 * 1024 * 1024)),  # 10MB
    "request_timeout": int(os.getenv("REQUEST_TIMEOUT", 30)),  # 30秒
    "allowed_origins": os.getenv("ALLOWED_ORIGINS", "*").split(",") if os.getenv("ALLOWED_ORIGINS") else ["*"],
    "debug_mode": os.getenv("DEBUG_MODE", "false").lower() == "true"
}

app = FastAPI(
    title="HTML转Markdown处理器API",
    description="将HTML内容转换为带占位符或不带占位符的Markdown格式",
    version="1.0.0",
    debug=CONFIG["debug_mode"]
)

# 更安全的CORS配置
if CONFIG["allowed_origins"] != ["*"]:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CONFIG["allowed_origins"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
else:
    # 开发环境使用通配符
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

class ProcessRequest(BaseModel):
    html_content: str
    url: Optional[str] = ""

    @field_validator('html_content')
    @classmethod
    def validate_html_content(cls, v):
        if not v or not v.strip():
            raise ValueError('HTML内容不能为空')

        if len(v.encode('utf-8')) > CONFIG["max_html_size"]:
            raise ValueError(f'HTML内容大小不能超过 {CONFIG["max_html_size"] // (1024*1024)}MB')

        return v

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        if v and v.strip():
            # 验证URL格式
            parsed = urllib.parse.urlparse(v)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError('URL格式无效')
        return v

class ProcessResponse(BaseModel):
    status: str
    # 带占位符的结果
    placeholder_html: str = ""
    placeholder_markdown: str = ""
    placeholder_text: str = ""
    placeholder_mapping: str = ""
    # 不带占位符的结果
    cl_content_html: str = ""
    cl_content_md: str = ""
    cl_content_text: str = ""
    # 从extract API获取的其他字段
    html_content: str = ""
    markdown_content: str = ""
    xpath: str = ""
    process_time: float = 0.0
    header_content_text: str = ""
    extract_success: bool = False

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
            if src :
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src)
                self.placeholder_mapping[placeholder] = full_src
                video['src'] = f"{{{{{placeholder}}}}}"

            # 替换poster属性
            poster = video.get('poster')
            if poster :
                full_poster = process_url(poster)
                placeholder = self._generate_placeholder(full_poster)
                self.placeholder_mapping[placeholder] = full_poster
                video['poster'] = f"{{{{{placeholder}}}}}"

        # 处理source标签
        for source in soup.find_all('source'):
            src = source.get('src')
            if src :
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src)
                self.placeholder_mapping[placeholder] = full_src
                source['src'] = f"{{{{{placeholder}}}}}"

        # 处理audio标签
        for audio in soup.find_all('audio'):
            src = audio.get('src')
            if src :
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
            # print(src)
            if src :
                full_src = process_url(src)
                placeholder = self._generate_placeholder(full_src)
                self.placeholder_mapping[placeholder] = full_src
                img['src'] = f"{{{{{placeholder}}}}}"

        # 处理a标签（下载链接）
        for a in soup.find_all('a'):
            href = a.get('href')
            print(href)
            if href and self.is_media_url(href):
                print(f"---{href}")
                full_href = process_url(href)
                placeholder = self._generate_placeholder(full_href)
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
                cl_content_html="",
                cl_content_md="",
                cl_content_text="",
                html_content="",
                markdown_content="",
                xpath="",
                process_time=0.0,
                header_content_text="",
                extract_success=False
            )

        # 处理base_url
        if url:
            base_url = process_base_url(url)
        else:
            base_url = ""

        # 调用API获取结果
        try:
            # 使用配置的API地址和超时时间
            api_url = CONFIG["extract_api_url"]
            timeout = CONFIG["request_timeout"]

            logger.info(f"调用API: {api_url}, 超时时间: {timeout}秒")

            response = requests.post(
                api_url,
                json={
                    "html_content": html_content,
                    "url": url
                },
                timeout=timeout,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "zprogressAPI/1.0.0"
                }
            )

            if response.status_code != 200:
                error_msg = f"API调用失败: HTTP {response.status_code}"
                try:
                    error_detail = response.json().get("error", response.text)
                    error_msg += f" - {error_detail}"
                except:
                    error_msg += f" - {response.text[:200]}"

                logger.error(error_msg)
                return ProcessResponse(
                    status=error_msg,
                    placeholder_html="",
                    placeholder_markdown="",
                    placeholder_text="",
                    placeholder_mapping="",
                    cl_content_html="",
                    cl_content_md="",
                    cl_content_text="",
                    html_content="",
                    markdown_content="",
                    xpath="",
                    process_time=0.0,
                    header_content_text="",
                    extract_success=False
                )

            # 验证响应内容
            try:
                result = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"API响应不是有效的JSON格式: {str(e)}")
                return ProcessResponse(
                    status=f"API响应格式错误: {str(e)}",
                    placeholder_html="",
                    placeholder_markdown="",
                    placeholder_text="",
                    placeholder_mapping="",
                    cl_content_html="",
                    cl_content_md="",
                    cl_content_text="",
                    html_content="",
                    markdown_content="",
                    xpath="",
                    process_time=0.0,
                    header_content_text="",
                    extract_success=False
                )

            # 不带占位符的结果
            html_without_holder = result.get("cl_content_html", "")
            md_without_holder = result.get("cl_content_md", "")
            text_without_holder = result.get("cl_content_text", "")

            # 从extract API获取的其他字段
            html_content = result.get("html_content", "")
            markdown_content = result.get("markdown_content", "")
            xpath = result.get("xpath", "")
            process_time = result.get("process_time", 0.0)
            header_content_text = result.get("header_content_text", "")
            extract_success = result.get("extract_success", False)

            if not html_without_holder and not md_without_holder:
                logger.warning("API返回的内容为空")

        except requests.exceptions.Timeout:
            error_msg = f"API请求超时 ({timeout}秒)"
            logger.error(error_msg)
            return ProcessResponse(
                status=error_msg,
                placeholder_html="",
                placeholder_markdown="",
                placeholder_text="",
                placeholder_mapping="",
                cl_content_html="",
                cl_content_md="",
                cl_content_text="",
                html_content="",
                markdown_content="",
                xpath="",
                process_time=0.0,
                header_content_text="",
                extract_success=False
            )
        except requests.exceptions.ConnectionError:
            error_msg = f"无法连接到API服务器: {CONFIG['extract_api_url']}"
            logger.error(error_msg)
            return ProcessResponse(
                status=error_msg,
                placeholder_html="",
                placeholder_markdown="",
                placeholder_text="",
                placeholder_mapping="",
                cl_content_html="",
                cl_content_md="",
                cl_content_text="",
                html_content="",
                markdown_content="",
                xpath="",
                process_time=0.0,
                header_content_text="",
                extract_success=False
            )
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

        # 生成占位符映射关系（转为json数组格式）
        placeholder_mapping_list = [{"placeholder": k, "original_url": v} for k, v in replacer.placeholder_mapping.items()]
        placeholder_mapping = json.dumps(placeholder_mapping_list, ensure_ascii=False, indent=2)

        return ProcessResponse(
            status="处理成功",
            placeholder_html=html_with_placeholders,
            placeholder_markdown=md_with_placeholders,
            placeholder_text=text_with_placeholders,
            placeholder_mapping=placeholder_mapping,
            cl_content_html=html_without_holder,
            cl_content_md=md_without_holder,
            cl_content_text=text_without_holder,
            html_content=html_content,
            markdown_content=markdown_content,
            xpath=xpath,
            process_time=process_time,
            header_content_text=header_content_text,
            extract_success=extract_success
        )

    except Exception as e:
        logger.error(f"处理出错: {str(e)}")
        return ProcessResponse(
            status=f"处理出错: {str(e)}",
            placeholder_html="",
            placeholder_markdown="",
            placeholder_text="",
            placeholder_mapping="",
            cl_content_html="",
            cl_content_md="",
            cl_content_text="",
            html_content="",
            markdown_content="",
            xpath="",
            process_time=0.0,
            header_content_text="",
            extract_success=False
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

# 全局异常处理中间件
@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """处理验证错误"""
    logger.warning(f"验证错误: {str(exc)}")
    return JSONResponse(
        status_code=422,
        content={
            "status": f"输入验证失败: {str(exc)}",
            "placeholder_html": "",
            "placeholder_markdown": "",
            "placeholder_text": "",
            "placeholder_mapping": "",
            "cl_content_html": "",
            "cl_content_md": "",
            "cl_content_text": "",
            "html_content": "",
            "markdown_content": "",
            "xpath": "",
            "process_time": 0.0,
            "header_content_text": "",
            "extract_success": False
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """处理未预期的异常"""
    logger.error(f"未处理的异常: {type(exc).__name__}: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={
            "status": f"服务器内部错误: {str(exc)}" if CONFIG["debug_mode"] else "服务器内部错误",
            "placeholder_html": "",
            "placeholder_markdown": "",
            "placeholder_text": "",
            "placeholder_mapping": "",
            "cl_content_html": "",
            "cl_content_md": "",
            "cl_content_text": "",
            "html_content": "",
            "markdown_content": "",
            "xpath": "",
            "process_time": 0.0,
            "header_content_text": "",
            "extract_success": False
        }
    )

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
        "zprogressAPI:app",
        host="0.0.0.0",
        port=8765,
        log_level="info"
    )



