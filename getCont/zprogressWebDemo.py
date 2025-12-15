import asyncio
import os
import re
import aiohttp
import aiofiles
import requests
import urllib.parse
from pathlib import Path
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter
import markdownify
from typing import Dict, List, Optional, Tuple
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

    async def download_file(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """
        异步下载文件到本地
        """
        try:
            if not url.startswith(('http://', 'https://')):
                if self.base_url:
                    url = urllib.parse.urljoin(self.base_url, url)
                else:
                    logger.warning(f"无法处理相对URL: {url}")
                    return None

            if url in self.downloaded_files:
                return self.downloaded_files[url]

            parsed_url = urllib.parse.urlparse(url)
            filename = os.path.basename(parsed_url.path)
            if not filename:
                filename = f"download_{len(self.downloaded_files)}"

            ext = os.path.splitext(filename)[1].lower()
            if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                subdir = self.output_dir / "images"
            elif ext in ['.mp4', '.avi', '.mov', '.wmv']:
                subdir = self.output_dir / "videos"
            elif ext in ['.pdf', '.doc', '.docx', '.txt']:
                subdir = self.output_dir / "documents"
            else:
                subdir = self.output_dir / "files"

            subdir.mkdir(exist_ok=True)
            file_path = subdir / filename

            counter = 1
            original_path = file_path
            while file_path.exists():
                stem = original_path.stem
                suffix = original_path.suffix
                file_path = subdir / f"{stem}_{counter}{suffix}"
                counter += 1

            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                response.raise_for_status()

                async with aiofiles.open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        await f.write(chunk)

                local_path = str(file_path.relative_to(self.output_dir.parent))
                self.downloaded_files[url] = local_path
                logger.info(f"文件下载完成: {local_path}")
                return local_path

        except Exception as e:
            logger.error(f"下载失败 {url}: {str(e)}")
            return None

    
    async def collect_download_urls(self, html_content: str) -> List[Tuple[str, str]]:
        """
        从HTML内容中收集所有需要下载的URL
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        download_tasks = []

        # 收集图片
        for img in soup.find_all('img'):
            src = img.get('src')
            if src:
                if not src.startswith(('http://', 'https://')) and self.base_url:
                    src = urllib.parse.urljoin(self.base_url, src)
                download_tasks.append(('img', src))

        # 收集视频相关链接
        for div in soup.find_all('div'):
            div_class = div.get('class') or []
            div_id = div.get('id') or ""
            div_class_str = " ".join(div_class) if isinstance(div_class, list) else str(div_class)

            # video标签
            video_tags = div.find_all("video")
            for video_tag in video_tags:
                video_src = video_tag.get('src')
                if video_src and video_src.endswith('.mp4'):
                    if not video_src.startswith(('http://','https://')) and self.base_url:
                        video_src = urllib.parse.urljoin(self.base_url, video_src)
                    download_tasks.append(('video_src', video_src))

                poster_src = video_tag.get('poster')
                if poster_src:
                    if not poster_src.startswith(('http://', 'https://')) and self.base_url:
                        poster_src = urllib.parse.urljoin(self.base_url, poster_src)
                    download_tasks.append(('poster', poster_src))

                source_tags = video_tag.find_all('source')
                for source_tag in source_tags:
                    source_src = source_tag.get('src')
                    if source_src:
                        if not source_src.startswith(('http://', 'https://')) and self.base_url:
                            source_src = urllib.parse.urljoin(self.base_url, source_src)
                        download_tasks.append(('video_source', source_src))

            # iframe视频
            if ("video" in div_class_str or "video" in str(div_id)) and not div.find_all("video"):
                video_url = None
                iframe = div.find('iframe')
                if iframe and iframe.get('src'):
                    video_url = iframe['src']
                else:
                    for tag in div.find_all(True, recursive=True):
                        for attr in ['src', 'data-src', 'href']:
                            val = tag.get(attr)
                            if val and isinstance(val, str) and ('.mp4' in val or 'player' in val or 'video' in val):
                                video_url = val
                                break
                        if video_url:
                            break

                if video_url:
                    if not video_url.startswith(('http://', 'https://')) and self.base_url:
                        video_url = urllib.parse.urljoin(self.base_url, video_url)
                    download_tasks.append(('video', video_url))

        # source标签
        for source in soup.find_all('source'):
            src = source.get('src')
            if src:
                if not src.startswith(('http://', 'https://')) and self.base_url:
                    src = urllib.parse.urljoin(self.base_url, src)
                download_tasks.append(('source', src))

        # 下载链接
        for a in soup.find_all('a'):
            href = a.get('href')
            if href and self.is_download_link(href):
                if not href.startswith(('http://', 'https://')) and self.base_url:
                    href = urllib.parse.urljoin(self.base_url, href)
                download_tasks.append(('a', href))

        return download_tasks

    async def download_all_files(self, download_tasks: List[Tuple[str, str]]) -> Dict[str, str]:
        """
        批量下载文件
        """
        url_to_local_path = {}

        print(f"=== 开始下载 {len(download_tasks)} 个文件 ===")
        async with aiohttp.ClientSession() as session:
            for i, (tag_type, url) in enumerate(download_tasks):
                print(f"下载任务 {i+1}/{len(download_tasks)}: {tag_type}, {url}")
                local_path = await self.download_file(session, url)
                if local_path:
                    url_to_local_path[url] = local_path
                    print(f"下载成功: {local_path}")
                else:
                    print(f"下载失败: {url}")

        return url_to_local_path

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
        
            # 2. 收集下载链接
            download_tasks = await self.collect_download_urls(html_content)

            # 3. 下载文件
            url_to_local_path = await self.download_all_files(download_tasks)

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

class URLPlaceholderReplacer:
    """
    独立的URL占位符替换器
    格式：{文件夹前缀}/{8位md5}_{8位哈希}.{扩展名}
    """

    def __init__(self):
        self.placeholder_mapping = {}  # 存储占位符与原始URL的对应关系
        self.url_counter = {}  # 用于处理相同URL的重复
        self.used_placeholders = set()  # 跟踪已使用的占位符，避免哈希冲突
        self.position_hashes = {}  # 存储每个URL的位置哈希

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

    def _generate_position_hash(self, url: str) -> str:
        """
        为URL的每个位置生成8位哈希标识
        相同URL的不同位置会有不同的8位哈希
        """
        if url not in self.position_hashes:
            self.position_hashes[url] = []

        # 生成基于URL和位置的哈希
        position = len(self.position_hashes[url])  # 当前位置索引
        unique_string = f"{url}_{position}_{random.randint(1000, 9999)}"

        # 生成8位哈希
        position_hash = hashlib.md5(unique_string.encode('utf-8')).hexdigest()[:8]

        # 确保哈希唯一（如果重复则重新生成）
        while position_hash in self.position_hashes[url]:
            unique_string = f"{url}_{position}_{random.randint(1000, 9999)}"
            position_hash = hashlib.md5(unique_string.encode('utf-8')).hexdigest()[:8]

        # 记录这个位置的哈希
        self.position_hashes[url].append(position_hash)

        return position_hash

    def _generate_placeholder(self, url: str, element_type: str = "") -> str:
        """
        生成占位符，格式：{8位md5}_{8位哈希}

        规则：
        1. 8位md5：URL的MD5的前8位（相同URL相同md5）
        2. 8位哈希：基于URL和位置的唯一哈希（相同URL不同位置不同哈希）
        """
        # 1. 生成URL的MD5（相同URL相同MD5）
        url_md5 = hashlib.md5(url.encode('utf-8')).hexdigest()
        file_md5 = url_md5[:8]      # 前8位作为md5部分

        # 2. 生成位置哈希（相同URL不同位置不同哈希）
        position_hash = self._generate_position_hash(url)

        placeholder = f"{file_md5}_{position_hash}"

        # 检查并确保占位符是唯一的
        counter = 1
        while placeholder in self.used_placeholders:
            # 发现冲突，添加一个数字后缀
            placeholder = f"{file_md5}_{position_hash}_{counter}"
            counter += 1

        self.used_placeholders.add(placeholder)

        return placeholder

    def replace_urls_with_placeholders(self, html_content: str, base_url: str = "") -> str:
        """
        将HTML中的媒体文件URL替换为占位符
        格式：{文件夹前缀}/{8位md5}_{8位哈希}.{扩展名}
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


    def save_mapping_to_file(self, output_file: str = "placeholder_mapping.json"):
        """
        将占位符与原始URL的对应关系保存到JSON文件
        """
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(self.placeholder_mapping, f, ensure_ascii=False, indent=2)
            print(f"✓ 占位符映射关系已保存到: {output_file}")
        except Exception as e:
            print(f"✗ 保存映射文件失败: {str(e)}")

    def process_html_file(self, input_file: str, output_file: str = None, mapping_file: str = None):
        """
        处理HTML文件，替换URL为占位符并保存映射关系

        Args:
            input_file: 输入HTML文件路径
            output_file: 输出HTML文件路径（可选，默认在原文件名基础上添加_placeholder）
            mapping_file: 映射关系文件路径（可选，默认为placeholder_mapping.json）
        """
        try:
            # 读取HTML文件
            with open(input_file, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            # 替换URL为占位符
            processed_html = self.replace_urls_with_placeholders(html_content)

            # 保存处理后的HTML
            if output_file is None:
                input_path = Path(input_file)
                output_file = str(input_path.parent / f"{input_path.stem}_placeholder{input_path.suffix}")

            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(processed_html)

            print(f"✓ 处理后的HTML已保存到: {output_file}")

            # 保存映射关系
            if mapping_file is None:
                mapping_file = "test_output_placeholder_mapping.json"
            self.save_mapping_to_file(mapping_file)

            return output_file, mapping_file

        except Exception as e:
            print(f"✗ 处理文件失败: {str(e)}")
            return None, None


async def test_placeholder_replacement():

    # 创建替换器实例
    replacer = URLPlaceholderReplacer()

    # 处理test_output.html文件
    input_file = "test_output.html"
    if os.path.exists(input_file):
        output_file, mapping_file = replacer.process_html_file(input_file)
        
        with open(output_file, 'r', encoding='utf-8') as f:
            content_html = f.read()
        
        converter = CustomMarkdownConverter(
            heading_style="ATX",
            bullets="*",
            strip=['script', 'style']
        )

        markdown_content = converter.convert(content_html)

        # 清理多余空行
        markdown_content = re.sub(r'\n\s*\n\s*\n', '\n\n', markdown_content)

        with open("test_output_placeholder.md", 'w', encoding='utf-8') as f:
            f.write(markdown_content)
    else:
        print(f"文件不存在: {input_file}")


async def main():
    example_html = ""
    with open("1.html", 'r', encoding='utf-8') as f:
        example_html = f.read()

    response = requests.post(
        "http://127.0.0.1:8321/extract",
        json={
            "html_content": example_html,
            "url":"https://www.gov.cn/zhengce/202510/content_7046643.htm"
            }
    )
    print("Status Code:", response.status_code)

    if response.status_code == 200:
        try:
            result = response.json()
            markdown_content = result.get("cl_content_md", response.text)  
            html_content = result.get("cl_content_html",response.text)

            # 第二类型
            html_without_holder = result.get("cl_content_html", response.text)
            md_without_holder = result.get("cl_content_md", response.text)
            text_without_holder = result.get("cl_content_text", response.text)
        except ValueError:
            markdown_content = response.text
    else:
        print("Request failed!")
        
    # 创建转换器实例
    converter = HTMLToMarkdownConverter(
        output_dir="downloads",
        base_url="https://www.gov.cn/zhengce/202510/"
    )

    # 执行转换
    try:
        output_file = await converter.convert_html_to_markdown(html_content, "test_output.md")

    except Exception as e:
        print(f"转换失败：{str(e)}")


def process_content(url_input, html_input):
    """
    处理输入的内容并返回结果
    """
    try:

        html_content = html_input
        url = url_input

        if not html_content:
            return "HTML内容不能为空", "", "", "", "", "", ""

        # 处理base_url
        if url:
            base_url = process_base_url(url)
        else:
            base_url = ""

        # 调用API获取结果
        response = requests.post(
            "http://192.168.182.41:8000/extract",
            json={
                "html_content": html_content,
                "url": url
            },
            timeout=30
        )

        if response.status_code != 200:
            return f"API调用失败: {response.status_code}", "", "", "", "", "", ""

        result = response.json()

        # 不带占位符的结果
        html_without_holder = result.get("cl_content_html", "")
        md_without_holder = result.get("cl_content_md", "")
        text_without_holder = result.get("cl_content_text", "")

        # 处理带占位符的结果
        replacer = URLPlaceholderReplacer()
        html_with_placeholders = replacer.replace_urls_with_placeholders(html_content)

        # 转换带占位符的Markdown
        converter = CustomMarkdownConverter(
            heading_style="ATX",
            bullets="*",
            strip=['script', 'style']
        )
        md_with_placeholders = converter.convert(html_with_placeholders)
        md_with_placeholders = clean_markdown_content(md_with_placeholders)

        # 生成带占位符的纯文本
        soup = BeautifulSoup(html_with_placeholders, 'html.parser')
        for script in soup(["script", "style"]):
            script.decompose()
        text_with_placeholders = soup.get_text()
        lines = (line.strip() for line in text_with_placeholders.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text_with_placeholders = '\n'.join(chunk for chunk in chunks if chunk)

        # 生成占位符映射关系
        placeholder_mapping = json.dumps(replacer.placeholder_mapping, ensure_ascii=False, indent=2)

        return ("处理成功",
                html_with_placeholders, md_with_placeholders, text_with_placeholders, placeholder_mapping,
                html_without_holder, md_without_holder, text_without_holder)

    except Exception as e:
        return f"处理出错: {str(e)}", "", "", "", "", "", ""

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

def create_gradio_interface():
    """
    创建Gradio界面
    """
    with gr.Blocks(title="HTML转Markdown处理器", theme=gr.themes.Soft()) as interface:
        gr.Markdown("# HTML转Markdown处理器")
        gr.Markdown("将HTML内容转换为带占位符或不带占位符的Markdown格式")

        with gr.Row():
            # 左侧输入面板
            with gr.Column(scale=1):
                gr.Markdown("## 输入参数")

                url_input = gr.Textbox(
                    label="URL地址",
                    placeholder="请输入URL地址，例如：https://example.com",
                    lines=1
                )

                html_json_input = gr.Textbox(
                    label="HTML内容",
                    placeholder='请输入HTML内容',
                    lines=15
                )

                process_btn = gr.Button("处理内容", variant="primary", size="lg")

                status_output = gr.Textbox(label="处理状态", interactive=False)

            # 右侧输出面板
            with gr.Column(scale=2):
                gr.Markdown("## 输出结果")

                with gr.Tabs():
                    # 带占位符的标签页
                    with gr.TabItem("带占位符"):
                        with gr.Tabs():
                            with gr.TabItem("HTML"):
                                placeholder_html_output = gr.Code(
                                    label="带占位符的HTML",
                                    language="html",
                                    lines=20,
                                    max_lines=30
                                )

                            with gr.TabItem("Markdown"):
                                placeholder_md_output = gr.Code(
                                    label="带占位符的Markdown",
                                    language="markdown",
                                    lines=20,
                                    max_lines=30
                                )

                            with gr.TabItem("纯文本"):
                                placeholder_text_output = gr.Code(
                                    label="带占位符的纯文本",
                                    language="text",
                                    lines=20,
                                    max_lines=30
                                )

                            with gr.TabItem("映射关系"):
                                placeholder_mapping_output = gr.Code(
                                    label="占位符映射关系 (JSON)",
                                    language="json",
                                    lines=20,
                                    max_lines=30
                                )

                    # 不带占位符的标签页
                    with gr.TabItem("不带占位符"):
                        with gr.Tabs():
                            with gr.TabItem("HTML"):
                                no_placeholder_html_output = gr.Code(
                                    label="不带占位符的HTML",
                                    language="html",
                                    lines=20,
                                    max_lines=30
                                )

                            with gr.TabItem("Markdown"):
                                no_placeholder_md_output = gr.Code(
                                    label="不带占位符的Markdown",
                                    language="markdown",
                                    lines=20,
                                    max_lines=30
                                )

                            with gr.TabItem("纯文本"):
                                no_placeholder_text_output = gr.Code(
                                    label="不带占位符的纯文本",
                                    language="text",
                                    lines=20,
                                    max_lines=30
                                )

        # 绑定处理函数
        process_btn.click(
            fn=process_content,
            inputs=[url_input, html_json_input],
            outputs=[
                status_output,
                placeholder_html_output,
                placeholder_md_output,
                placeholder_text_output,
                placeholder_mapping_output,
                no_placeholder_html_output,
                no_placeholder_md_output,
                no_placeholder_text_output
            ]
        )

        # 添加示例
        gr.Markdown("## 使用示例")
        gr.Examples(
            examples=[
                [
                    "https://www.gov.cn/zhengce/202510/content_7046643.htm",
                    '{\n  "html_content": "<html><body><h1>测试标题</h1><p>测试内容</p><img src=\\"test.jpg\\" /><video src=\\"test.mp4\\"></video></body></html>",\n  "url": "https://www.gov.cn/zhengce/202510/content_7046643.htm"\n}'
                ]
            ],
            inputs=[url_input, html_json_input]
        )

    return interface

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

def process_frontend_content(url_input, html_json_input):
    """
    前端处理函数
    """
    try:
        html_content = html_json_input
        url = url_input

        # 处理base_url
        base_url = process_base_url(url) if url else ""
        html_without_holder = ""
        # 调用API获取第二类型的结果（不带占位符）
        try:
            response = requests.post(
                "http://127.0.0.1:8321/extract",
                json={
                    "html_content": html_content,
                    "url": url
                },
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                # 第二类型：不带占位符的结果
                html_without_holder = result.get("cl_content_html", "")
                md_without_holder = result.get("cl_content_md", "")
                text_without_holder = result.get("cl_content_text", "")
            else:
                html_without_holder = f"API调用失败: {response.status_code}"
                md_without_holder = f"API调用失败: {response.status_code}"
                text_without_holder = f"API调用失败: {response.status_code}"
        except Exception as e:
            html_without_holder = f"API调用出错: {str(e)}"
            md_without_holder = f"API调用出错: {str(e)}"
            text_without_holder = f"API调用出错: {str(e)}"

        # 处理第一类型：带占位符的结果
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

        return ("处理成功",
                html_with_placeholders, md_with_placeholders, text_with_placeholders, placeholder_mapping,
                html_without_holder, md_without_holder, text_without_holder)

    except Exception as e:
        return f"处理出错: {str(e)}", "", "", "", "", "", ""

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

        # 添加示例
        gr.Examples([
            [
                "https://www.gov.cn/zhengce/202510/content_7046643.htm",
                '{\n  "html_content": "<html><body><h1>标题</h1><img src=\\"test.jpg\\"/></body></html>",\n  "url": "https://www.gov.cn/zhengce/202510/content_7046643.htm"\n}'
            ]
        ], inputs=[url_input, html_input])

    return interface

if __name__ == "__main__":
    # 启动Gradio界面
    interface = create_simple_gradio_interface()
    interface.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False
    )


# 输出的文件有两个类型的，
# 第一个类型是有占位符的：分别输出四个文件，有占位符的html，有占位符的md，文件和占位符的匹配关系文件，有占位符的纯文本。
# 第二个类型的是无占位符的：分别输出三个文件，无占位符的html，无占位符的md，无占位符的纯文本。

