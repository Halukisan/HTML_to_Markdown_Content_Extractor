import re
import string
import logging
from datetime import datetime
from lxml import html as lxml_html
import html
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import markdownify
from markdownify import MarkdownConverter
import uvicorn
from bs4 import BeautifulSoup, Comment,Tag
import logging
import os
from logging.handlers import RotatingFileHandler
import hashlib
import json
import urllib.parse
from urllib.parse import parse_qs, urlencode, urlunparse
from typing import Set

def setup_logging():
    log_level = logging.WARNING  
    
    logging.basicConfig(
        level=log_level,
        format='%(levelname)s - %(message)s',  
        handlers=[
            logging.StreamHandler()  
        ]
    )
    
    return logging.getLogger(__name__)


logger = setup_logging()

app = FastAPI(
    title="HTML to Markdown Content Extractor",
    description="Extract main content from HTML and convert to Markdown",
    version="3.0.0"
)
class CustomMarkdownConverter(MarkdownConverter):

    def __init__(self,**options):
        super().__init__(**options)

    def convert_video(self,el,text,convert_as_inline=False,**kwargs):
        el['width'] = '100%'
        el['controls'] = 'controls'
        if 'style' in el.attrs:
            del el['style']
        return f'\n{str(el)}\n'
    
    def convert_table(self, el, text, conversion_args=None,**kwargs):

        el['width'] = '100%'
        el['border'] = '1'
        el['cellspacing'] = '0'
        
        if 'style' in el.attrs:
            del el['style']


        html_output = str(el)

        return f'\n{html_output}\n'
    def convert_source(self,el,text,convert_as_inline=False,**kwargs):

        return ""

    def convert_button(self, el, text, convert_as_inline=False, **kwargs):
        src = el.get('path')
        
        AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac'}
        
        is_audio = src and any(ext in src.lower() for ext in AUDIO_EXTENSIONS)

        if not is_audio:
            return text 

        safe_src = html.escape(src)
        audio_html = f'\n<audio controls preload="metadata"><source src="{safe_src}"></audio>\n'
        
        return audio_html

    def convert_audio(self, el, text, convert_as_inline=False, **kwargs):

        src = el.get('src')
        if not src:
            source_tag = el.find('source', src=True)
            if source_tag:
                src = source_tag.get('src')

        if not src:
            return ""  

        safe_src = html.escape(src)
        return f'\n<audio controls preload="metadata"><source src="{safe_src}"></audio>\n'
        

def delete_short_tags(soup: BeautifulSoup, tag_text: str) -> None:

    elements_to_delete = []
    file_extensions = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.rar', '.txt', '.csv','.mp3','.mp4'}
    file_exts_lower = {ext.lower() for ext in file_extensions}

    url_pattern = re.compile(r'\b(?:https?://|www\.)[^\s<>"]+', re.IGNORECASE)

    for element in soup.find_all(string=re.compile(re.escape(tag_text))):
        if not hasattr(element, 'parent') or element.parent is None:
            continue

        parent = element.parent
        if not hasattr(parent, 'decompose'):
            continue

        try:
            parent_text = parent.get_text(strip=True)
        except Exception:
            continue

        has_attachment = False

        if parent.name == 'a' and parent.has_attr('href'):
            href = parent.get('href', '').strip()
            if href:
                href_lower = href.lower()
                if any(ext in href_lower for ext in file_exts_lower):
                    has_attachment = True

        if not has_attachment:
            for link in parent.find_all('a', href=True):
                href = link.get('href', '').strip()
                if not href:
                    continue
                href_lower = href.lower()
                if any(ext in href_lower for ext in file_exts_lower):
                    has_attachment = True
                    break

        if not has_attachment:
            for url in url_pattern.findall(parent_text):
                url_lower = url.lower()
                if any(ext in url_lower for ext in file_exts_lower):
                    has_attachment = True
                    break

        if has_attachment:
            continue  

        if tag_text not in parent_text:
            continue

        if len(parent_text) >= 50:
            continue

        lower_text = parent_text.lower()
        forbidden_keywords = {'文章', '内容', '正文', '详情', '更多信息', '附件', '.pdf', '中心'}
        if any(kw in lower_text for kw in forbidden_keywords):
            continue

        if parent.name in {'span', 'div', 'a', 'button', 'p', 'dt', 'li', 'h4', 'font'}:
            if parent.name == 'button':
                path_attr = parent.get('path')
                if path_attr:
                    path_lower = path_attr.lower()
                    if any(ext in path_lower for ext in ('.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac')):
                        pass
                    else:
                        elements_to_delete.append(parent)
                else:
                    elements_to_delete.append(parent)
            else:
                elements_to_delete.append(parent)  
        
    for elem in elements_to_delete:
        try:
            if elem and hasattr(elem, 'decompose'):
                elem.decompose()
        except Exception as e:
            logger.warning(f"delete_short_tags 删除失败: {e}")

def clean_table_html(table_html: str) -> str:

    try:
        table_soup = BeautifulSoup(table_html, 'html.parser')

        essential_attributes = {
            'table': [],
            'thead': [],
            'tbody': [],
            'tr': [],
            'th': ['colspan', 'rowspan'],
            'td': ['colspan', 'rowspan'],
            'img': ['src', 'alt'],  
            'video': ['src', 'poster', 'controls'],  
            'audio': ['src', 'controls'],  
            'source': ['src', 'type']  
        }

        def clean_style_attribute(style_value: str) -> str:
            if not style_value:
                return ""

            semantic_keywords = ['font-weight', 'font-style', 'text-decoration']
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


def remove_empty_tags(soup: BeautifulSoup) -> None:

    tags_to_keep_empty = {'br', 'hr', 'img', 'input', 'embed', 'area', 'base', 'col', 'frame', 'link', 'meta', 'param', 'source', 'track', 'wbr','video'}
    MEDIA_EXTENSIONS = {
        '.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac',
        '.mp4', '.webm', '.mov', '.avi', '.mkv', '.flv', '.m3u8'
    }
    changed = True
    while changed:
        changed = False
        for tag in soup.find_all(True):
            if tag.name in tags_to_keep_empty:
                continue

            has_content = False

            if tag.get_text(strip=True):
                has_content = True

            if not has_content and tag.find_all():
                for child in tag.find_all(True):
                    if child.name in tags_to_keep_empty:
                        has_content = True
                        break

            if not has_content and tag.name == 'button':
                path_attr = tag.get('path')
                if path_attr:
                    path_lower = path_attr.lower()
                    if any(ext in path_lower for ext in MEDIA_EXTENSIONS):
                        has_content = True

            if not has_content:
                tag.decompose()
                changed = True
                break


def clean_html_content_advanced(html_content: str) -> str:

    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        for tag in soup.find_all(['script', 'style', 'meta', 'link', 'noscript','el-button']):
            tag.decompose()

        tags_to_delete = [
            "已阅","字号", "打印", "关闭", "收藏","分享到微信","分享","字体","小","中","大","s92及gd格式的文件请用SEP阅读工具",
            "扫一扫在手机打开当前页", "扫一扫在手机上查看当前页面","用微信“扫一扫”","分享给您的微信好友",
            "相关链接",'下载文字版','下载图片版','扫一扫在手机打开当前页面',"微信扫一扫：分享","上一篇","下一篇","【打印文章】","返回顶部","回到顶部",
            "你的浏览器不支持video","当前位置：","微信里点“发现”，扫一下","浏览次数：","您当前的位置：",'返回上一页',"您现在是游客状态"
        ]
        
        for tag_text in tags_to_delete:
            delete_short_tags(soup, tag_text)

        error_elements_to_delete = []

        for element in soup.find_all(string=re.compile("我要纠错")):
            if not hasattr(element, 'parent') or element.parent is None:
                continue

            parent = element.parent

            if not hasattr(parent, 'get_text') or not hasattr(parent, 'decompose'):
                continue

            try:
                if parent and len(parent.get_text(strip=True)) < 20:  
                    error_elements_to_delete.append(parent)
            except Exception:
                continue

        for parent in error_elements_to_delete:
            try:
                if parent and hasattr(parent, 'decompose'):
                    parent.decompose()
            except Exception:
                pass
        essential_attributes = {
            'div': [], 'p': [], 'span': [],
            'table': ['border', 'cellpadding', 'cellspacing'],
            'tr': [], 'td': ['colspan', 'rowspan'], 'th': ['colspan', 'rowspan'],
            'ul': [], 'ol': [], 'li': [],
            'a': ['href', 'target'],
            'img': ['src'],
            'video': ['src', 'poster', 'controls'],
            'source': ['src'],
            'iframe': ['src'],
            'br': [], 'hr': [],
            'button':['path'],
            'audio':[]
        }

        def clean_attributes(tag):
            if tag.name is None:
                return

            allowed_attrs = essential_attributes.get(tag.name, [])

            if tag.has_attr('style'):
                del tag['style']

            attrs_to_remove = [attr for attr in tag.attrs if attr not in allowed_attrs]
            for attr in attrs_to_remove:
                del tag[attr]

        for tag in soup.find_all(True):
            clean_attributes(tag)

        for table in soup.find_all('table'):
            cleaned_table = clean_table_html(str(table))
            table.replace_with(BeautifulSoup(cleaned_table, 'html.parser'))
        for img in soup.find_all('img'):
            if img:
                src = img.get('src', '')
                if not src or 'base64' in src.lower() or 'data:image' in src.lower():
                    img.decompose()
        containers = soup.find_all('caizhikeji_iframe')

        for container in containers:
            video = container.find('video')
            if video:
                v_parent = video.parent
                if v_parent:
                    v_parent.replace_with(video)

            audio = container.find('audio')
            if audio:
                mp3_path = None
                target_btn = container.find('button', attrs={'path': True})
                
                if target_btn and '.mp3' in target_btn['path'].lower():
                    mp3_path = target_btn['path']
                
                if mp3_path:
                    new_audio = soup.new_tag('audio', attrs={'controls': 'controls', 'preload': 'metadata'})
                    source = soup.new_tag('source', attrs={'src': mp3_path, 'type': 'audio/mpeg'})
                    new_audio.append(source)

                    audio_parent = audio.parent

                    if audio_parent:
                        audio_parent_sibling = audio_parent.find_next_sibling()
                        if audio_parent_sibling:
                            audio_parent_sibling.decompose()

                        audio_parent.replace_with(new_audio)
            container.name = 'div'
        remove_empty_tags(soup)

        return str(soup)

    except Exception as e:
        return html_content

def remove_invisible_tags(soup: BeautifulSoup):
    file_extensions = {
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        '.txt', '.csv',
        '.jpg', '.jpeg', '.png',
        '.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a',
        '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv', '.m4v',
        '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2',
        '.exe', '.msi', '.dmg', '.pkg', '.apk', '.ipa'
    }
    file_exts_lower = {ext.lower() for ext in file_extensions}

    for tag in soup(['script', 'style', 'noscript','svg', 'meta', 'link', 'input']):
        tag.decompose()

    for tag in soup('iframe'):
        should_keep = False

        if tag.get('src'):
            src = tag.get('src').lower()
            if any(src.endswith(ext) for ext in file_exts_lower):
                should_keep = True

        if not should_keep:
            iframe_content = tag.get_text() + str(tag)
            iframe_content_lower = iframe_content.lower()
            if any(ext in iframe_content_lower for ext in file_exts_lower):
                should_keep = True

        if not should_keep:
            tag.decompose()

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    for hidden in soup.find_all(attrs={"hidden": True}):
        hidden.decompose()
    style_pattern = re.compile(r'(display\s*:\s*none)|(visibility\s*:\s*hidden)', re.IGNORECASE)
    for tag in soup.find_all(attrs={"style": True}):
        if style_pattern.search(tag['style']):
            tag.decompose()

    hidden_classes = ['pchide', 'hide', 'invisible', 'd-none','hidden']
    selector = ','.join(f'.{cls}' for cls in hidden_classes)
    for tag in soup.select(selector):
        tag.decompose()

def remove_duplicate_metadata_elements(soup, table_element):
    if not table_element:
        return soup, 0

    table_text = clean_text(table_element.get_text())
    if not table_text:
        return soup, 0

    metadata_keywords = [
        '发文机关', '发文字号', '发文日期', '成文日期', '发布日期', '主题分类',
        '公文种类', '来源', '索引号', '标题', '文号', '签发人','发布机构','体裁分类','组配分类',
        '发布单位'
    ]

    extracted_phrases = []

    for keyword in metadata_keywords:
        keyword_pattern = f'{keyword}[^，。；；\n]*'
        matches = re.findall(keyword_pattern, table_text)
        for match in matches:
            cleaned_match = clean_text(match)
            if len(cleaned_match) > 5:  
                extracted_phrases.append(cleaned_match)

    if not extracted_phrases:
        return soup, 0

    removed_count = 0

    for phrase in extracted_phrases:

        matching_divs = []
        all_uls = soup.find_all('ul')
        all_tables = soup.find_all('tbody')

        for div in all_uls:
            if div in table_element.parents:
                continue

            div_text = clean_text(div.get_text())
            if phrase in div_text:
                matching_divs.append(div)

        for tbody in all_tables:
            if tbody == table_element or tbody in table_element.descendants:
                continue

            tbody_text = clean_text(tbody.get_text())
            if phrase in tbody_text:
                matching_divs.append(tbody)

        for div in matching_divs:
            div_text = clean_text(div.get_text())

            keyword_count = sum(1 for kw in metadata_keywords if kw in div_text)
            matched_phrases_count = sum(1 for p in extracted_phrases if p in div_text)

            if keyword_count >= 2 or matched_phrases_count >= 2:
                div.decompose()
                removed_count += 1
            else:
                logger.debug(f"DEBUG: div匹配但元数据较少，保留")

    return soup, removed_count

def clean_text(text: str) -> str:
    if not text: return ""
    return ''.join(text.split())

def get_element_score(element) -> int:

    if not element or not isinstance(element, Tag):
        return 0
        
    text = clean_text(element.get_text())
    if not text: return 0
    if len(text) > 700: return 0
    meta_keywords = ['索引号', '主题分类', '发文字号', '发文机关','发文机构', '文号','组配分类','成文日期', '发布日期', '公文种类', '浏览次数', '来源：', '来源:']
    if sum(1 for kw in meta_keywords if kw in text) >= 1:
        if sum(1 for kw in meta_keywords if kw in text) >= 2 or element.name == 'table':
            return 2
        return 2

    if len(text) < 200:
        ui_keywords = ['首页', '主页', '打印',"保存", '关闭', '收藏', '字号', '扫一扫', '分享','来源：', '当前位置','当前位置：', '位置：', '位置:',"发布时间"]
        if any(kw in text for kw in ui_keywords):
            return 1
        if '>' in element.get_text() and len(text) < 100:
            return 1
            
    return 0

def is_content_start(element) -> bool:
    if not element: return False
    text = clean_text(element.get_text())
    
    if len(text) > 150 and get_element_score(element) == 0:
        return True
    
    if isinstance(element, Tag) and element.name == 'p' and len(text) > 50 and get_element_score(element) == 0:
        return True
        
    return False

def has_heading_tags(element: Tag, max_depth: int = 3) -> bool:

    if element.name and element.name.lower() in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
        return True

    if max_depth > 0:
        for child in element.find_all(recursive=False):
            if has_heading_tags(child, max_depth - 1):
                return True

    return False


def has_content_indicators(element: Tag) -> bool:

    text_content = element.get_text(strip=True)
    if len(text_content) > 200:
        if element.find('p') or element.find_all(text=lambda text: len(text.strip()) > 50):
            return True

    content_tags = ['article', 'main', 'section', 'div.content', 'div.main-content']
    element_classes = element.get('class', [])

    for tag in content_tags:
        if tag in element_classes:
            return True

    return False


def analyze_content_structure(element: Tag) -> dict:

    scores = {
        'heading_score': 0,      # 
        'content_score': 0,      # 
        'structure_score': 0,    # 
        'total_score': 0         # 
    }

    if not element:
        return scores

    heading_tags = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'title', 'header']
    if element.name in heading_tags:
        scores['heading_score'] += 1

    element_classes = element.get('class', [])
    element_id = element.get('id', '')
    text_lower = clean_text(element.get_text()).lower()

    text = clean_text(element.get_text())
    if 5 <= len(text) <= 100:  
        scores['heading_score'] += 1

    if len(text) > 50:
        scores['content_score'] += min(len(text) // 100, 5)  

    paragraphs = element.find_all('p')
    scores['content_score'] += min(len(paragraphs), 3)  

    links = element.find_all('a')
    scores['content_score'] += min(len(links), 2)  

    content_containers = ['article', 'main', 'section', 'content', 'body']
    if element.name in content_containers:
        scores['structure_score'] += 3

    content_classes = [
        'content', 'article', 'post', 'entry', 'main', 'body',
        'text', 'paragraph', 'description'
    ]
    for cls in content_classes:
        if cls in element_classes:
            scores['structure_score'] += 2
            break

    depth = len(list(element.parents))
    if depth > 3:  
        scores['structure_score'] += 1

    scores['total_score'] = (
        scores['heading_score'] * 2 +  
        scores['content_score'] +
        scores['structure_score']
    )

    return scores


def check_by_punctuation(soup: BeautifulSoup, cutoff_element: Tag, html_content: str) -> bool:

    try:
        cutoff_str = str(cutoff_element)
        cutoff_pos = html_content.find(cutoff_str)

        if cutoff_pos == -1:
            import re
            simplified_cutoff_str = re.sub(r'\s+style="[^"]*"', '', cutoff_str)
            simplified_cutoff_str = re.sub(r'\s+class="[^"]*"', '', simplified_cutoff_str)
            simplified_cutoff_str = re.sub(r'\s+id="[^"]*"', '', simplified_cutoff_str)
            simplified_cutoff_str = re.sub(r'\s+', ' ', simplified_cutoff_str)
            cutoff_pos = html_content.find(simplified_cutoff_str)

            if cutoff_pos == -1:
                logger.debug(f"WARNING: 无法在原始HTML中找到分界点，分界点类型: {cutoff_element.name}")
                logger.debug(f"DEBUG: 分界点内容预览: {cutoff_str[:200]}...")
                logger.debug(f"DEBUG: 原始HTML长度: {len(html_content)}")
                text_content = clean_text(cutoff_element.get_text())
                if text_content and len(text_content) > 5:  
                    prefix = text_content[:15]
                    prefix = prefix.rstrip()
                    if prefix:
                        text_pos = html_content.find(prefix)
                        if text_pos != -1:
                            cutoff_pos = text_pos
                        else:
                            return False
                else:
                    return False
    except Exception as e:
        return False

    content_before = html_content[:cutoff_pos]

    if len(content_before) > 0:
        logger.debug(f"DEBUG: content_before 前100字符 = {content_before[:100]}")
    else:
        logger.debug("DEBUG: content_before 为空！")

    soup_before = BeautifulSoup(content_before, 'html.parser')

    text_before = clean_text(soup_before.get_text())

    if len(text_before) > 0:
        logger.debug(f"DEBUG: text_before 内容 = {text_before[:50]}")
    else:
        raw_text = soup_before.get_text()
        if len(raw_text) > 0:
            logger.debug(f"DEBUG: 原始文本 = {raw_text[:50]}")

    if len(text_before) == 0 and len(content_before) > 0:
        content_indicators = [
            'class="content"',
            'class="article"',
            'class="main"',
            'article',
            'main'
        ]

        for indicator in content_indicators:
            if indicator in content_before.lower():
                return True

    if len(text_before) < 50:  
        return False

    chinese_punctuation = ['，', '。', '？', '！', '；', '、', '～', '…', '—']
    english_punctuation = [',', '.', '?', '!', ';', "'", '"']

    punctuation_count = 0
    for char in text_before:
        if char in chinese_punctuation or char in english_punctuation:
            punctuation_count += 1

    punctuation_density = punctuation_count / len(text_before) * 100

    has_sentence_enders = any(p in text_before for p in ['。', '！', '？', '.', '!', '?'])
    if has_sentence_enders:
        return True

    if punctuation_count >= 3:
        return True

    if punctuation_density > 1.0:
        return True

    sentences = text_before.split('。')  
    for sentence in sentences:
        if len(sentence) > 20 and '，' in sentence:
            return True

    return False


def check_content_before_cutoff_v2(soup: BeautifulSoup, cutoff_element: Tag, html_content: str) -> bool:
    try:
        cutoff_str = str(cutoff_element)
        cutoff_pos = html_content.find(cutoff_str)

        if cutoff_pos == -1:
            import re
            simplified_cutoff_str = re.sub(r'\s+style="[^"]*"', '', cutoff_str)
            simplified_cutoff_str = re.sub(r'\s+class="[^"]*"', '', simplified_cutoff_str)
            simplified_cutoff_str = re.sub(r'\s+id="[^"]*"', '', simplified_cutoff_str)
            simplified_cutoff_str = re.sub(r'\s+', ' ', simplified_cutoff_str)
            cutoff_pos = html_content.find(simplified_cutoff_str)

            if cutoff_pos == -1:                
                text_content = clean_text(cutoff_element.get_text())
                if text_content and len(text_content) > 5:  
                    prefix = text_content[:15]
                    prefix = prefix.rstrip()
                    if prefix:
                        text_pos = html_content.find(prefix)
                        if text_pos != -1:
                            cutoff_pos = text_pos
                        else:
                            return False
                else:
                    return False
    except Exception as e:
        return False

    content_before = html_content[:cutoff_pos]

    soup_before = BeautifulSoup(content_before, 'html.parser')

    if check_by_punctuation(soup, cutoff_element, html_content):
        return True
    else:
        logger.debug("DEBUG: 标点符号检测未发现正文特征，继续其他检测方法")

    potential_titles = []

    for element in soup_before.find_all(['div', 'p', 'span', 'strong', 'b', 'td', 'th']):
        scores = analyze_content_structure(element)
        if scores['heading_score'] >= 3:  
            potential_titles.append((element, scores))

    if potential_titles:
        return True

    content_after = html_content[cutoff_pos + len(str(cutoff_element)):]
    soup_after = BeautifulSoup(content_after, 'html.parser')

    text_before = clean_text(soup_before.get_text())
    text_after = clean_text(soup_after.get_text())

    if len(text_before) > len(text_after) * 0.5:  
        return True

    paragraphs = soup_before.find_all('p')
    if len(paragraphs) >= 2:  
        total_paragraph_text = ''.join([clean_text(p.get_text()) for p in paragraphs])
        if len(total_paragraph_text) > 200:  
            return True

    semantic_tags = ['article', 'main', 'section', 'aside', 'nav']
    for tag in semantic_tags:
        if soup_before.find(tag):
            return True

    for element in soup_before.find_all(['div', 'section', 'article', 'p']):
        scores = analyze_content_structure(element)
        if scores['total_score'] >= 8:  
            return True

    return False

 
def split_header_and_content_v2(html_content: str) -> tuple[str, str]:

    if not html_content:
        return '', ''
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
    except Exception as e:
        return '', html_content

    remove_invisible_tags(soup)

    tables = soup.find_all('table')
    table_element = None

    for table in tables:
        if get_element_score(table) == 2:
            table_element = table
            soup, metadata_removed_count = remove_duplicate_metadata_elements(soup, table_element)
            break

    divs = soup.find_all('div')
    for div in divs:
        if get_element_score(div) == 2:
            div_class = ' '.join(div.get('class', []))
            div_id = div.get('id', '')
            div_aria_label = div.get('aria-label', '')

            content_keywords = [
                'content', 'maincontent', 'article', 'main', 'text', 'detail',
                'article-content', 'post-content', 'entry-content', 'page-content',
                'content-area', 'main-content', 'article-body', 'post-body'
            ]

            div_class_lower = div_class.lower()
            div_id_lower = div_id.lower()
            div_aria_label_lower = div_aria_label.lower()

            is_content_container = False
            if 'aria-label="文章正文"' in str(div) or div_aria_label_lower == '文章正文':
                is_content_container = True
            else:
                for keyword in content_keywords:
                    if keyword in div_class_lower or keyword in div_id_lower:
                        is_content_container = True
                        break

            if not is_content_container:
                table_element = div

    uls = soup.find_all('ul')
    for ul in uls:
        if get_element_score(ul) == 2:
            table_element = ul

    if not table_element:
        breadcrumbs = []
        for element in soup.find_all(['div', 'nav', 'p', 'span']):
            if get_element_score(element) == 1:
                breadcrumbs.append(element)

        if breadcrumbs:
            cutoff_element = breadcrumbs[0]
        else:
            return '', str(soup)
    else:

        found_breadcrumb_by_upward = False
        current = table_element.parent

        while current and current.name not in ['body', 'html', '[document]']:
            for child in current.children:
                if isinstance(child, Tag) and child != table_element:
                    if get_element_score(child) == 1:  
                        found_breadcrumb_by_upward = True
                        break
            if found_breadcrumb_by_upward:
                break
            current = current.parent

        if found_breadcrumb_by_upward:
            cutoff_element = table_element
        else:
            logger.debug("DEBUG: 向上扩散未找到面包屑，使用正则匹配")

            breadcrumb_patterns = [
                r'[^>]*>[^>]*>[^>]*',  # 
                r'.*?首页.*?>.*',     # 
                r'.*?当前位置.*',      # 
                r'.*?位置[：:].*',     # 
                # 
                r'来源[：:].*?发布时间[：:].*',     # +
                r'来源[：:].*?[0-9]{4}-[0-9]{2}-[0-9]{2}',  # +
                r'发布时间[：:].*',    # 
                r'来源[：:].*',        # 
                r'.*?政府.*?发布时间.*',     # +
                r'.*?办公室.*发布时间.*',     # +
                r'[0-9]{4}-[0-9]{2}-[0-9]{2}.*?(?:打印|保存|分享|收藏)',
                r'[0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}.*?(?:打印|保存|分享)',
                r'.*?来源.*?时间.*',          # +
                r'.*?发布.*?日期.*',          # +
                r'.*?编辑.*?时间.*'           # +
            ]

            found_breadcrumb_by_regex = False
            breadcrumb_element = None

            soup_for_regex = BeautifulSoup(html_content, 'html.parser')
            remove_invisible_tags(soup_for_regex)

            for pattern in breadcrumb_patterns:
                matches = soup_for_regex.find_all(string=re.compile(pattern))
                if matches:
                    for match in matches:
                        parent = match.parent
                        if parent and get_element_score(parent) == 1:
                            breadcrumb_element = parent
                            found_breadcrumb_by_regex = True
                            break
                    if found_breadcrumb_by_regex:
                        break

            if found_breadcrumb_by_regex:
                cutoff_element = breadcrumb_element
            else:
                cutoff_element = table_element

    if cutoff_element:
        has_content_before = check_content_before_cutoff_v2(soup, cutoff_element, html_content)
        if has_content_before:
            content_html = str(soup)
            cleaned_content_html = clean_html_content_advanced(content_html)
            return '', cleaned_content_html
        else:
            logger.debug("DEBUG: 分界点之前未检测到正文内容，继续执行header提取")


    if cutoff_element.name in ['tr', 'td', 'th']:
        table = cutoff_element.find_parent('table')
        if table:
            cutoff_element = table

    try:
        str(cutoff_element)
    except Exception as e:
        text_content = cutoff_element.get_text() if hasattr(cutoff_element, 'get_text') else ''
        if text_content:
            cutoff_element = BeautifulSoup(f'<div>{text_content}</div>', 'html.parser').div
        else:
            return '', html_content

    all_header_elements = []

    for table in soup.find_all('table'):
        if get_element_score(table) == 2:
            try:
                str(table)
                all_header_elements.append(table)
            except:
                logger.debug("DEBUG: 跳过有问题的表格元素")

    for element in soup.find_all(['div', 'nav', 'p', 'span']):
        if get_element_score(element) == 1:
            try:
                str(element)
                all_header_elements.append(element)
            except:
                logger.debug("DEBUG: 跳过有问题的面包屑元素")


    elements_to_extract = []

    if cutoff_element.name == 'table' or cutoff_element.name == 'div' or get_element_score(cutoff_element) == 2:
        elements_to_extract.append(cutoff_element)

        for header_elem in all_header_elements:
            if header_elem != cutoff_element and get_element_score(header_elem) == 1:
                table_pos = html_content.find(str(cutoff_element))
                breadcrumb_pos = html_content.find(str(header_elem))

                if breadcrumb_pos < table_pos:
                    elements_to_extract.append(header_elem)

    elif get_element_score(cutoff_element) == 1:
        elements_to_extract.append(cutoff_element)

        for header_elem in all_header_elements:
            if header_elem != cutoff_element:
                breadcrumb_pos = html_content.find(str(cutoff_element))
                elem_pos = html_content.find(str(header_elem))

                if elem_pos < breadcrumb_pos:
                    elements_to_extract.append(header_elem)

    elements_to_extract = list({id(elem): elem for elem in elements_to_extract}.values())

    elements_to_extract.sort(key=lambda x: html_content.find(str(x)))

    header_parts = []
    processed_ids = set()

    for elem in elements_to_extract:
        if id(elem) not in processed_ids:
            processed_ids.add(id(elem))
            try:
                elem_str = str(elem)
                if elem_str and elem_str.strip():
                    header_parts.append(elem_str)
                elem.decompose()
            except Exception as e:
                logger.debug(f"DEBUG: 提取元素时出错: {e}")
                try:
                    text_content = elem.get_text() if hasattr(elem, 'get_text') else ''
                    if text_content:
                        header_parts.append(f'<div>{text_content}</div>')
                    elem.decompose()
                except:
                    logger.debug(f"DEBUG: 无法提取元素内容，跳过")

    header_html = '\n'.join(header_parts)
    content_html = str(soup)


    return header_html, content_html

def clean_html_content_with_split(html_content: str) -> str:
    header_html, content_html = split_header_and_content_v2(html_content)

    cleand_header_html = clean_html_content_advanced(header_html)
    cleaned_content_html = clean_html_content_advanced(content_html)

    content_md = html_to_markdown_simple(cleaned_content_html)

    content_soup = BeautifulSoup(cleaned_content_html, 'html.parser')
    header_soup = BeautifulSoup(cleand_header_html,'html.parser')
    content_text = clean_text(content_soup.get_text())
    header_text = clean_text(header_soup.get_text())

    return header_text, cleaned_content_html, content_md, content_text

# 

# 
class HTMLInput(BaseModel):
    html_content: str
    url: str = ""  #
    need_placeholder: bool = False  # 是否启用资源替换为占位符的服务
    xpath: str = ""  # 可选的xpath参数，如果提供则直接使用xpath获取内容，跳过正文定位 
    
class MarkdownOutput(BaseModel):
    # markdown_content: str
    html_content: str
    # xpath: str
    status: str
    # process_time: float

    header_content_text: str = ""

    cl_content_html: str = ""
    cl_content_md: str = ""
    content_text: str = ""

    extract_success: bool = False

    placeholder_html: str = ""
    placeholder_markdown: str = ""
    # placeholder_text: str = ""
    placeholder_mapping: str = ""

class SimpleMarkdownInput(BaseModel):
    html_content: str
    url: str = ""

class SimpleMarkdownOutput(BaseModel):
    success: bool
    placeholder_markdown: str = ""
    placeholder_mapping: str = ""
     

def remove_header_footer_by_content_traceback(body):
    
    header_content_keywords = [
        '登录', '注册', '首页', '主页', '无障碍', '办事', '无障碍浏览','打印','收藏','机构概况','在线服务','互动交流',
        '走进', '移动版', '手机版', '导航', '菜单', '搜索', '市政府',
        'login', 'register', 'home', 'menu', 'search', 'nav'
    ]
    
    footer_content_keywords = [
        '网站说明', '网站标识码', '版权所有', '主办单位', '承办单位', 
        '技术支持', '联系我们', '网站地图', '隐私政策', '免责声明',
        '备案号', 'icp', '公安备案', '政府网站', '网站管理',
        'copyright', 'all rights reserved', 'powered by', 'designed by'
    ]
    
    header_elements = []
    for keyword in header_content_keywords:
        xpath = f"//*[contains(text(), '{keyword}')]"
        elements = body.xpath(xpath)
        header_elements.extend(elements)
    
    footer_elements = []
    for keyword in footer_content_keywords:
        xpath = f"//*[contains(text(), '{keyword}')]"
        elements = body.xpath(xpath)
        footer_elements.extend(elements)
    
    containers_to_remove = set()
    
    for element in header_elements:
        container = find_header_footer_container(element)
        if container and container not in containers_to_remove:
            containers_to_remove.add(container)
    
    for element in footer_elements:
        container = find_footer_container_by_traceback(element)
        if container and container not in containers_to_remove:
            containers_to_remove.add(container)
    
    header_divs = body.xpath(".//div[.//header] | .//div[.//footer] | .//div[.//nav]")
    for div in header_divs:
        div_text = div.text_content().lower()
        
        header_count = sum(1 for keyword in header_content_keywords if keyword in div_text)
        footer_count = sum(1 for keyword in footer_content_keywords if keyword in div_text)
        
        if header_count >= 2 or footer_count >= 2:
            if div not in containers_to_remove:
                containers_to_remove.add(div)    
    removed_count = 0
    for container in containers_to_remove:
        try:
            parent = container.getparent()
            if parent is not None:
                parent.remove(container)
                removed_count += 1
        except Exception as e:
            logger.error(f"删除容器时出错: {e}")
    
    return body

def find_header_footer_container(element):
    current = element
    
    while current is not None and current.tag != 'html':
        if current.tag in ['div', 'section', 'header', 'footer', 'nav', 'aside']:
            classes = current.get('class', '').lower()
            elem_id = current.get('id', '').lower()
            tag_name = current.tag.lower()
            
            header_indicators = ['header', 'nav', 'navigation', 'menu', 'topbar', 'banner', 'menubar', 'head']
            footer_indicators = ['footer', 'foot', 'bottom', 'end', 'copyright', 'links', 'sitemap', 'contact']
            
            for indicator in header_indicators + footer_indicators:
                if (indicator in classes or indicator in elem_id or indicator in tag_name):
                    return current
        
        parent = current.getparent()
        if parent is None or parent.tag in ['html', 'head', 'body', 'script', 'meta']:
            break
        
        current = parent
    

    if (element.getparent() and 
        element.getparent().tag == 'div' and 
        element.getparent().getparent() and 
        element.getparent().getparent().tag in ['body', 'html']):
        
        div_element = element.getparent()
        div_text = div_element.text_content().lower()
        
        header_content_keywords = [
            '登录', '注册', '首页', '主页', '无障碍',  '办事',  '无障碍浏览','打印','收藏','机构概况','在线服务','互动交流', 
            '走进', '移动版', '手机版', '导航', '菜单', '搜索', '市政府','读屏专用','ALT+Shift'
        ]
        
        footer_content_keywords = [
            '网站说明', '网站标识码', '版权所有', '主办单位', '承办单位', 
            '技术支持', '联系我们', '网站地图', '隐私政策', '免责声明',
            '备案号', 'icp', '公安备案', '政府网站', '网站管理','退出服务','鼠标样式','阅读方式'
        ]
        
        header_count = sum(1 for keyword in header_content_keywords if keyword in div_text)
        footer_count = sum(1 for keyword in footer_content_keywords if keyword in div_text)
        
        if header_count >= 2 or footer_count >= 2:
            return div_element
    
    if element.getparent() and element.getparent().tag != 'html':
        return element.getparent()
    
    return None
def find_footer_container_by_traceback(element):
    current = element
    
    while current is not None:
        if current.tag in ['div', 'section', 'footer']:
            classes = current.get('class', '').lower()
            elem_id = current.get('id', '').lower()
            
            footer_indicators = ['footer', 'foot', 'bottom', 'end', 'copyright']
            for indicator in footer_indicators:
                if indicator in classes or indicator in elem_id:
                    return current
        
        parent = current.getparent()
        if parent is None or parent.tag in ['html', 'head', 'body', 'script', 'meta']:
            break
            
        current = parent
    
    return None

def remove_html_comments(element):
    count = 0
    comments = list(element.xpath('.//comment()'))
    for elem in comments:
        parent = elem.getparent()
        if parent is not None:
            parent.remove(elem)
            count += 1
    if count == 0:
        html_str = lxml_html.tostring(element,encoding='unicode')
        original_length = len(html_str)

        html_str = re.sub(r'<!--.*?-->', '', html_str, flags=re.DOTALL)

        clended_length = len(html_str)

        if clended_length < original_length:
            logger.info(f"🧹 remove_html_comments: 正则删除注释 ({original_length} -> {clended_length} 字符)")
    return count


def preprocess_html_remove_interference(page_tree):

    body_elements = page_tree.xpath("//body")
    if body_elements:
        body = body_elements[0]
    else:
        body = page_tree

    if body is None:
        return None

    def clean_comment_text(node):
        if hasattr(node, 'tag') and node.tag == lxml_html.html.Comment:
            parent = node.getparent()
            if parent is not None:
                comment_text = node.text if node.text else ''
                node.text = ''
                return True

        for child in list(node):
            if clean_comment_text(child):
                node.remove(child)

        return False

    cleaned_count = 0
    for comment in list(body.xpath('.//comment()')):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
            cleaned_count += 1

    display_none_count = remove_display_none_elements(body)
    removed_count = remove_page_level_header_footer(body)
    
    cleaned_html = lxml_html.tostring(body, encoding='unicode', pretty_print=True)
    
    return body

def remove_display_none_elements(body):
   
    removed_count = 0

    elements_to_remove = []

    all_candidates = body.xpath(".//*[@style or contains(concat(' ', normalize-space(@class), ' '), ' ng-hide ')]")

    for element in all_candidates:
        style = element.get('style', '').lower()
        classes = element.get('class', '')

        if (style and 'display' in style and 'none' in style and
            re.search(r'display\s*:\s*none', style, re.IGNORECASE)) or \
           (' ng-hide ' in f" {classes} "):
            elements_to_remove.append(element)
    for element in elements_to_remove:
        elem_id = element.get('id', '')
        elem_class = element.get('class', '')
        style = element.get('style', '').lower()
        if style and 'display' in style and 'none' in style:
            logger.info(f"  标记删除不可见元素(display:none): {element.tag} id='{elem_id[:30]}' class='{elem_class[:30]}'")
        else:
            logger.info(f"  标记删除不可见元素(ng-hide): {element.tag} id='{elem_id[:30]}' class='{elem_class[:30]}'")

    for element in elements_to_remove:
        try:
            parent = element.getparent()
            if parent is not None:
                elem_id = element.get('id', '')
                elem_class = element.get('class', '')
                child_count = len(element.xpath(".//*"))
                
                parent.remove(element)
                removed_count += 1
                
        except Exception as e:
            logger.error(f"删除不可见元素时出错: {e}")
    
    
    return removed_count

def remove_page_level_header_footer(body):
    
    removed_count = 0

    select_based_to_remove = []

    all_containers = body.xpath(".//div | .//header | .//footer | .//nav")    
    
    select_keywords = {'市', '省', '县', '区', '自治州', '局', '厅', '政府',
                       '简体', '繁体', '中文', 'english', '语言', '版本', '手机版', '电脑版'}

    for container in all_containers:
        if container in select_based_to_remove:
            continue
            
        text_len = len((container.text_content() or '').strip())

        selects = container.xpath(".//select")
        if selects:
            for select in selects:
                options = select.xpath(".//option")
                if len(options) < 3:
                    continue

                option_texts = [opt.text.strip() for opt in options if opt.text]
                if not option_texts:
                    continue

                match_count = 0
                for txt in option_texts:
                    if any(kw in txt for kw in select_keywords):
                        match_count += 1
                        if match_count >= 2:  
                            break

                if match_count >= 2:
                    select_based_to_remove.append(container)
                    sample = ' | '.join(option_texts[:3])
                    break  

        if container not in select_based_to_remove:
            uls = container.xpath(".//ul | .//ol")
            for ul in uls:
                lis = ul.xpath("./li")
                if len(lis) < 4:
                    continue

                match_count = 0
                for li in lis:
                    if li.text:
                        li_text = li.text.strip()
                        if any(kw in li_text for kw in select_keywords):
                            match_count += 1
                            if match_count >= 3:
                                break

                if match_count >= 3:
                    select_based_to_remove.append(container)
                    sample = ' | '.join([li.text.strip() for li in lis[:3] if li.text])
                    break
    seen = set()
    unique_to_remove = []
    for elem in select_based_to_remove:
        if elem is not None:  
            eid = id(elem)
            if eid not in seen:
                seen.add(eid)
                unique_to_remove.append(elem)

    for container in unique_to_remove:
        try:
            if container is None or not hasattr(container, 'getparent'):
                continue
            
            container_text = (container.text_content() or '').strip()
            text_length = len(container_text)
            
            if text_length > 500:  
                
                selects_in_container = container.xpath(".//select")
                for select in selects_in_container:
                    select_parent = select.getparent()
                    if select_parent is not None:
                        select_parent_text = (select_parent.text_content() or '').strip()
                        if len(select_parent_text) < 200:  
                            select_parent_grandparent = select_parent.getparent()
                            if select_parent_grandparent is not None:
                                select_parent_grandparent.remove(select_parent)
                                removed_count += 1
                        else:
                            select_parent.remove(select)
                            removed_count += 1
                
                nav_uls = container.xpath(".//ul | .//ol")
                for ul in nav_uls:
                    lis = ul.xpath("./li")
                    if len(lis) >= 4:  
                        ul_text = (ul.text_content() or '').strip()
                        nav_keyword_count = sum(1 for kw in select_keywords if kw in ul_text)
                        if nav_keyword_count >= 3:
                            ul_parent = ul.getparent()
                            if ul_parent is not None:
                                ul_parent_text = (ul_parent.text_content() or '').strip()
                                if len(ul_parent_text) < 300:  
                                    ul_grandparent = ul_parent.getparent()
                                    if ul_grandparent is not None:
                                        ul_grandparent.remove(ul_parent)
                                        removed_count += 1
                                else:
                                    ul_parent.remove(ul)
                                    removed_count += 1
                continue
            
            parent = container.getparent()
            if parent is not None:
                parent.remove(container)
                removed_count += 1
        except Exception as e:
            logger.error(f"第0轮删除时出错: {e}")
    semantic_tags = ["//header", "//footer", "//nav"]
    for tag_xpath in semantic_tags:
        elements = body.xpath(tag_xpath)
        for element in elements:
            try:
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
                    removed_count += 1
            except Exception as e:
                logger.info(f"删除语义标签时出错: {e}")
    
    top_divs = body.xpath("./div") 

    containers_to_remove = []
    
    for div in top_divs:
        classes = div.get('class', '').lower()
        elem_id = div.get('id', '').lower()
        text_content = div.text_content().lower()
        
        is_header_footer = False
        
        strong_header_indicators = [
            'header', 'top', 'navbar', 'navigation', 'menu-main', 
            'site-header', 'page-header', 'banner', 'topbar'
        ]
        
        strong_footer_indicators = [
            'footer', 'bottom', 'site-footer', 'page-footer', 
            'footerpc', 'wapfooter', 'g-bottom'
        ]
        
        for indicator in strong_header_indicators + strong_footer_indicators:
            if indicator in classes or indicator in elem_id:
                is_header_footer = True
                break
        
        if not is_header_footer:
            header_words = [
                '登录', '注册', '首页', '主页', '无障碍', '办事', 
                '走进', '移动版', '手机版', '导航', '菜单', '搜索', '市政府',
                'login', 'register', 'home', 'menu', 'search', 'nav'
            ]
            header_count = sum(1 for word in header_words if word in text_content)
            
            footer_words =  [
                '网站说明', '网站标识码', '版权所有', '主办单位', '承办单位', 
                '技术支持', '联系我们', '网站地图', '隐私政策', '免责声明',
                '备案号', 'icp', '公安备案', '政府网站', '网站管理',
                'copyright', 'all rights reserved', 'powered by', 'designed by'
            ]
            footer_count = sum(1 for word in footer_words if word in text_content)
            
            text_length = len(text_content.strip())
            
            if header_count >= 4 and text_length < 1000:
                paragraphs = div.xpath(".//p")
                long_paragraphs = [p for p in paragraphs if len((p.text_content() or '').strip()) > 100]
                
                if len(long_paragraphs) <= 2:  
                    is_header_footer = True
                else:
                    logger.info(f"  跳过可能的正文容器: {header_count}个header关键词但包含{len(long_paragraphs)}个长段落")
                    
            elif footer_count >= 3 and text_length < 800:
                paragraphs = div.xpath(".//p")
                long_paragraphs = [p for p in paragraphs if len((p.text_content() or '').strip()) > 100]
                
                if len(long_paragraphs) <= 1:  
                    is_header_footer = True
                else:
                    logger.info(f"  跳过可能的正文容器: {footer_count}个footer关键词但包含{len(long_paragraphs)}个长段落")

        if is_header_footer:
            containers_to_remove.append(div)
    
    for container in containers_to_remove:
        try:
            if container is None :
                continue
                
            parent = container.getparent()
            if parent is not None:
                parent.remove(container)
                removed_count += 1
                class_attr = container.get('class', '') if container.get('class') else ''
        except Exception as e:
            logger.error(f"删除页面级容器时出错: {e}")
    
    return removed_count


def calculate_text_density(element):

    text_content = element.text_content().strip()
    text_length = len(text_content)
    
    if text_length == 0:
        return 0
    
    all_tags = element.xpath(".//*")
    tag_count = len(all_tags)
    
    links = element.xpath(".//a[@href]")
    link_count = 0
    for link in links:
        href = link.get('href', '').strip().lower()
        if (href and href != '#' and not href.startswith('javascript:')
            and not href.startswith(('mailto:', 'tel:', 'sms:', 'data:'))
            and 'void(' not in href):
            link_count += 1
    

    images = element.xpath(".//img")
    image_count = len(images)
    

    denominator = max(1, tag_count + link_count * 2 + image_count * 0.5)
    density = text_length / denominator
    
    return density

def remove_low_density_containers(body):
        
    top_level_containers = body.xpath("./div | ./section | ./main | ./article | ./header | ./footer | ./nav | ./aside")
    
    containers_to_remove = []
    
    for container in top_level_containers:
        density = calculate_text_density(container)
        text_length = len(container.text_content().strip())
        links = container.xpath(".//a")
        
        classes = container.get('class', '').lower()
        elem_id = container.get('id', '').lower()
        
        important_indicators = [
            'content', 'main', 'article', 'detail', 'news', 'info',
            'bg-fff', 'bg-white', 'wrapper', 'body'  
        ]
        
        has_important_content = any(indicator in classes or indicator in elem_id 
                                  for indicator in important_indicators)
        
        has_article_features = bool(
            container.xpath(".//h1 | .//h2 | .//h3") or  
            container.xpath(".//*[contains(text(), '发布时间') or contains(text(), '来源') or contains(text(), '浏览次数')]") or  
            len(container.xpath(".//p")) > 3  
        )
        
        if has_important_content or has_article_features:
            continue
        
        link_ratio = len(links) / max(1, len(container.xpath(".//*")))
        
        is_low_quality = False
        
        if density < 5 and link_ratio > 0.3:
            is_low_quality = True
        
        elif text_length < 200 and len(container.xpath(".//*")) > 20:
            is_low_quality = True
        
        elif links and text_length < 500:  #
            link_text_length = sum(len(link.text_content()) for link in links)
            if text_length > 0 and link_text_length / text_length > 0.8:  
                is_low_quality = True
        
        if is_low_quality:
            containers_to_remove.append(container)
    
    removed_count = 0
    for container in containers_to_remove:
        try:
            parent = container.getparent()
            if parent is not None:
                parent.remove(container)
                removed_count += 1
        except Exception as e:
            logger.error(f"删除低密度容器时出错: {e}")
    
    return body

def remove_semantic_interference_tags(body):
    
    semantic_tags_to_remove = [
        "//header", "//footer", "//nav", "//aside",
        "//div[@role='navigation']", "//div[@role='banner']", "//div[@role='contentinfo']",
        "//section[@role='navigation']"
    ]
    
    removed_count = 0
    for xpath in semantic_tags_to_remove:
        elements = body.xpath(xpath)
        for element in elements:
            try:
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
                    removed_count += 1
            except Exception as e:
                logger.info(f"删除语义标签时出错: {e}")
    
    return body

def remove_positional_interference(body):
    
    direct_children = body.xpath("./div | ./section | ./main | ./article")
    
    if len(direct_children) <= 2:
        return body
    
    containers_to_remove = []
    
    first_container = direct_children[0] if direct_children else None
    last_container = direct_children[-1] if len(direct_children) > 1 else None
    
    if first_container is not None:
        if is_positional_header(first_container):
            containers_to_remove.append(first_container)
    
    if last_container is not None and last_container != first_container:
        if is_positional_footer(last_container):
            containers_to_remove.append(last_container)
    
    removed_count = 0
    for container in containers_to_remove:
        try:
            parent = container.getparent()
            if parent is not None:
                parent.remove(container)
                removed_count += 1
        except Exception as e:
            logger.error(f"删除位置容器时出错: {e}")
    
    return body

def is_positional_header(container):
    text_content = container.text_content().lower()
    
    header_indicators = [
        '登录', '注册', '首页', '主页', '导航', '菜单', '搜索',
        '政务服务', '办事服务', '互动交流', '走进', '无障碍',
        'login', 'register', 'home', 'menu', 'search', 'nav'
    ]
    
    header_count = sum(1 for word in header_indicators if word in text_content)
    
    density = calculate_text_density(container)
    
    return header_count >= 3 or (density < 8 and header_count >= 2)

def is_positional_footer(container):
    text_content = container.text_content().lower()
    
    footer_indicators = [
        '版权所有', '主办单位', '承办单位', '技术支持', '联系我们',
        '网站地图', '隐私政策', '免责声明', '备案号', 'icp',
        '网站标识码', '政府网站', '网站管理',
        'copyright', 'all rights reserved', 'powered by'
    ]
    
    footer_count = sum(1 for word in footer_indicators if word in text_content)
    
    density = calculate_text_density(container)
    
    return footer_count >= 2 or (density < 6 and footer_count >= 1)

def is_interference_container(container):
    
    classes = container.get('class', '').lower()
    elem_id = container.get('id', '').lower()
    tag_name = container.tag.lower()
    text_content = container.text_content().lower()
    
    if tag_name in ['header', 'footer', 'nav', 'aside']:
        return True
    
    strong_interference_keywords = [
        'header', 'footer', 'nav', 'navigation', 'menu', 'menubar', 
        'topbar', 'bottom', 'sidebar', 'aside', 'banner', 'breadcrumb'
    ]
    
    for keyword in strong_interference_keywords:
        if keyword in classes or keyword in elem_id:
            return True
    
    density = calculate_text_density(container)
    text_length = len(text_content.strip())
    
    if density < 3 and text_length < 300:
        return True
    
    links = container.xpath(".//a[@href]")
    valid_links = []
    for link in links:
        href = link.get('href', '').strip().lower()
        if (href and href != '#' and not href.startswith('javascript:')
            and not href.startswith(('mailto:', 'tel:', 'sms:', 'data:'))
            and 'void(' not in href):
            valid_links.append(link)

    if len(valid_links) > 5:
        link_text_length = sum(len(link.text_content()) for link in valid_links)
        if text_length > 0:
            link_ratio = link_text_length / text_length
            if link_ratio > 0.7:
                return True
    
    header_content_patterns = [
        '登录', '注册', '首页', '主页', '无障碍', '政务服务', '办事服务',
        '互动交流', '走进', '移动版', '手机版', '导航', '菜单', '搜索',
        'login', 'register', 'home', 'menu', 'search', 'nav'
    ]
    
    footer_content_patterns = [
        '网站说明', '网站标识码', '版权所有', '主办单位', '承办单位',
        '技术支持', '联系我们', '网站地图', '隐私政策', '免责声明',
        '备案号', 'icp', '公安备案', '政府网站', '网站管理',
        'copyright', 'all rights reserved', 'powered by'
    ]
    
    header_matches = sum(1 for pattern in header_content_patterns if pattern in text_content)
    footer_matches = sum(1 for pattern in footer_content_patterns if pattern in text_content)
    
    if header_matches >= 2:  
        return True
    
    if footer_matches >= 2:  
        return True

    if text_length < 200 and (header_matches + footer_matches) >= 2:
        return True
    
    ad_keywords = ['advertisement', 'ads', 'social', 'share', 'follow', 'subscribe']
    ad_matches = sum(1 for keyword in ad_keywords if keyword in text_content or keyword in classes)
    if ad_matches >= 2:
        return True
    
    return False

def find_article_container(page_tree):

    cleaned_body = preprocess_html_remove_interference(page_tree)

    if cleaned_body is None:
        return None, None

    original_body = page_tree.xpath("//body")
    original_body = original_body[0] if original_body else None

    main_content = find_main_content_in_cleaned_html(cleaned_body, original_body)
    
    return main_content, cleaned_body

def extract_content_to_markdown(html_content: str):
    
    result = {
        'markdown_content': '',
        'html_content': '',
        'xpath': '',
        'status': 'failed'
    }

    try:
        if not html_content or not isinstance(html_content, str):
            return result

        comment_pattern = re.compile(r'<!--[\s\S]*?-->')
        original_comments = comment_pattern.findall(html_content)

        original_length = len(html_content)
        html_content = re.sub(r'<!--[\s\S]*?-->', '', html_content)
        removed = original_length - len(html_content)


        tree = lxml_html.fromstring(html_content)

        main_container, cleaned_body = find_article_container(tree)

        if main_container is None or cleaned_body is None:
            return result

        try:
            xpath = generate_xpath(main_container)
            if not xpath:
                xpath = ""
        except Exception as e:
            xpath = ""

        try:
            container_html = lxml_html.tostring(main_container, encoding='unicode', pretty_print=True)
            if not container_html:
                container_html = html_content
        except Exception as e:
            container_html = html_content

        try:
            cleaned_container_html = clean_container_html(container_html)
            if not cleaned_container_html:
                cleaned_container_html = container_html
        except Exception as e:
            cleaned_container_html = container_html

        try:
            markdown_content = markdownify.markdownify(
                cleaned_container_html,
                heading_style="ATX",  
                bullets="-",   
                strip=['script', 'style']  
            )

            if markdown_content:
                markdown_content = clean_markdown_content(markdown_content)
            else:
                markdown_content = ""

        except Exception as e:
            markdown_content = ""

        result.update({
            'markdown_content': markdown_content,
            'html_content': cleaned_container_html,
            'xpath': xpath,
            'status': 'success'
        })


        return result

    except Exception as e:
        import traceback
        error_msg = str(e) if str(e) else repr(e)

        return result
def remove_pua_chars(text:str)->str:

    if not text:
        return text

    def is_pua(char):
        code = ord(char)
        return (
            0xE000 <= code <= 0xF8FF or
            0xF0000 <= code <= 0xFFFFD or
            0x100000 <= code <= 0x10FFFD
        )

    return ''.join(c for c in text if not is_pua(c))

def clean_container_html(container_html: str) -> str:

    if not container_html or not isinstance(container_html, str):
        return container_html or ""

    try:
        original_length = len(container_html)
        container_html = re.sub(r'<!--.*?-->', '', container_html, flags=re.DOTALL)
        
        soup = BeautifulSoup(container_html, 'html.parser')
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()
        for script in soup.find_all('script'):
            if script:  
                script.decompose()
        
        for style in soup.find_all('style'):
            if style:  
                style.decompose()

        for img in soup.find_all('img'):
            if img:
                src = img.get('src', '')
                if 'base64' in src.lower():
                    img.decompose()

        styled_elements = soup.find_all(attrs={"style": True})
        
        display_none_elements = []
        for i, element in enumerate(styled_elements):
            style = element.get('style', '')
            if 'display' in style.lower() and 'none' in style.lower():
                display_none_elements.append(element)
                        
        for element in display_none_elements:
            try:
                element.decompose()
            except Exception as e:
                pass
        result = str(soup)
        
        if 'display:none' in result.lower():
            
            remaining = re.findall(r'<[^>]*display\s*:\s*none[^>]*>', result, re.IGNORECASE)

        all_tags = soup.find_all()
        for tag in all_tags:
            if tag is None or not hasattr(tag, 'attrs'):
                continue
                
            attrs_to_remove = []
            for attr_name in list(tag.attrs.keys()):  
                if attr_name.startswith('on'):  
                    attrs_to_remove.append(attr_name)
                elif (attr_name == 'href' and 
                      tag.get(attr_name) and 
                      str(tag[attr_name]).startswith('javascript:')):
                    attrs_to_remove.append(attr_name)
            
            for attr in attrs_to_remove:
                try:
                    del tag[attr]
                except (AttributeError, KeyError):
                    pass  
        cleaned_html = str(soup)
        cleaned_html = remove_pua_chars(cleaned_html)
        return cleaned_html
        
    except Exception as e:
        return container_html
def clean_markdown_content(markdown_content: str) -> str:

    if not markdown_content:
        return ""
    markdown_content = markdown_content.replace('\\n', '\n')

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

def find_main_content_in_cleaned_html(cleaned_body, original_body=None):    
    if cleaned_body is None:
        return None
    
    content_containers = cleaned_body.xpath(".//div | .//section | .//article | .//main")
    
    if not content_containers:
        return cleaned_body
    
    scored_containers = []
    containers_to_remove = []
    
    for container in content_containers:
        if container is None:
            continue
            
        score = calculate_content_container_score(container)
        
        classes = container.get('class', '').lower()
        elem_id = container.get('id', '').lower()
        
        is_protected = (
            'content' in elem_id.lower() or  
            container.xpath(".//*[@id='Content'] | .//*[@id='content']") or  
            'bg-fff' in classes or  
            'container' in classes and len(container.xpath(".//*")) > 20  
        )
        
        if is_protected:
            scored_containers.append((container, max(score, 50)))  
        elif score < -100:
            containers_to_remove.append(container)
        elif score > -50:  
            scored_containers.append((container, score))
    
    
    if not scored_containers:
        return content_containers[0]
    
    scored_containers.sort(key=lambda x: x[1], reverse=True)
    
    top_5 = scored_containers[:5]
    for idx, (container, score) in enumerate(top_5, 1):
        classes = container.get('class', '')
        elem_id = container.get('id', '')
        text_length = len(container.text_content().strip())
        child_count = len(container.xpath(".//*"))
        
  
    
    best_score = scored_containers[0][1]

    top_5_containers = scored_containers[:5]
    long_content_containers = []
    
    for container, score in top_5_containers:
        text_length = len(get_clean_text_content_lxml(container).strip())
        classes = container.get('class', '')
        elem_id = container.get('id', '')
        if classes == 'Article Article-wz':
            score+=60
        if text_length > 1000:  
            long_content_containers.append((container, score, text_length))
           
    if len(long_content_containers) >= 2:
        scores = [score for _, score, _ in long_content_containers]
        max_score = max(scores)
        min_score = min(scores)
        score_diff = max_score - min_score

        if score_diff <= 200:  
            
            long_content_containers.sort(key=lambda x: len(x[0].xpath(".//*")))
            
            selected_precise_container = None
            selected_text_length = 0  
            for container, score, text_length in long_content_containers:
                child_count = len(container.xpath(".//*"))
                classes = container.get('class', '')
                elem_id = container.get('id', '')
                            
                if child_count >= 10 or text_length > 3000:
                    selected_precise_container = container
                    selected_text_length = text_length  
                    break
            
            if selected_precise_container is not None:
                def find_meaningful_parent(element):
              
                    current = element.getparent()
                    depth = 0
                    max_depth = 5  
                    
                    meaningful_tags = ['div','section', 'article', 'main']
                    
                    while current is not None and depth < max_depth:
                        tag = current.tag.lower()
                        classes = current.get('class', '').strip()
                        elem_id = current.get('id', '').strip()
                        
                        
                        if tag == 'body':
                            break
                        
                        is_meaningful_tag = tag in meaningful_tags
                        has_identifier = bool(classes or elem_id)
                        
                        if is_meaningful_tag and has_identifier:
                            return current, depth + 1
                        elif not is_meaningful_tag:
                            logger.info(f"      ⏭ 跳过无意义标签: {tag}")
                        elif not has_identifier:
                            logger.info(f"      ⏭ 跳过无标识符的容器")
                        
                        current = current.getparent()
                        depth += 1
                    
                    return None, 0
                
                parent_container, parent_depth = find_meaningful_parent(selected_precise_container)
                
                if parent_container is not None:
                    parent_classes = parent_container.get('class', '')
                    parent_id = parent_container.get('id', '')
                    parent_text_length = len(parent_container.text_content().strip())
                    parent_child_count = len(parent_container.xpath(".//*"))

                    if parent_container.tag.lower() == 'body':
                        best_container = selected_precise_container
                    else:
                        parent_combined = f"{parent_classes} {parent_id}".lower()
                        has_interference = any(keyword in parent_combined for keyword in
                                             ['header', 'footer', 'nav', 'menu', 'sidebar'])

                        if not has_interference and parent_text_length > selected_text_length * 0.8:
                            best_container = parent_container
                        else:
                            best_container = selected_precise_container
                            if has_interference:
                                logger.info(f"   ⚠ 父容器包含干扰特征，保持精确容器")
                            else:
                                logger.info(f"   ⚠ 父容器内容差异过大，保持精确容器")
                else:
                    best_container = selected_precise_container
            else:
                best_container = scored_containers[0][0]
        else:
            best_container = scored_containers[0][0]
    else:
        
        score_threshold = 20
        similar_score_containers = [(container, score) for container, score in scored_containers 
                                   if abs(score - best_score) <= score_threshold]
        
        
        if len(similar_score_containers) > 1:
            best_container = select_best_container_prefer_child(
                [c for c, s in similar_score_containers], 
                scored_containers
            )
        else:
            best_container = scored_containers[0][0]
    
    def has_interference_keywords(container):
        classes = container.get('class', '').lower()
        elem_id = container.get('id', '').lower()
        combined = f"{classes} {elem_id}"
        
        interference_keywords = ['header', 'footer', 'nav', 'navigation', 'menu', 'sidebar']
        
        for keyword in interference_keywords:
            if keyword in combined:
                return True, keyword
        return False, None
    
    has_interference, keyword = has_interference_keywords(best_container)
    
    if has_interference:
       
        for container, score in scored_containers:
            has_interference_check, _ = has_interference_keywords(container)
            if not has_interference_check and score > 0:
                best_container = container
                break
        else:
            logger.info(f"   ⚠️ 未找到合适的替代容器，保持原选择（但可能不准确）")
    
    try:
        final_score = next(score for container, score in scored_containers if container == best_container)
        recalculated = False
    except StopIteration:
        final_score = calculate_content_container_score(best_container)
        recalculated = True

        if scored_containers and final_score == scored_containers[0][1]:
            text_content = best_container.text_content().strip()
            text_length = len(text_content)
            have_muchLinks = False

            if text_length > 0:
                links = best_container.xpath(".//a[@href]")
                link_count = 0
                for link in links:
                    href = link.get('href', '').strip().lower()
                    if (href and href != '#' and not href.startswith('javascript:')
                        and not href.startswith(('mailto:', 'tel:', 'sms:', 'data:'))
                        and 'void(' not in href):
                        link_count += 1

                if link_count >= 5:
                    have_muchLinks = True

            if have_muchLinks:
                best_container = scored_containers[0][0]
                final_score = scored_containers[0][1]
                recalculated = False
    
    final_text_length = len(best_container.text_content().strip())
    final_child_count = len(best_container.xpath(".//*"))
    
    return best_container
def is_child_of(child_element, parent_element):
    current = child_element.getparent()
    while current is not None:
        if current == parent_element:
            return True
        current = current.getparent()
    return False
def has_document_attachments(container):
    doc_extensions = {'.pdf', '.doc', '.docx', '.xls', '.xlsx'}

    for link in container.xpath(".//a[@href]"):
        href = link.get('href', '').lower()
        for ext in doc_extensions:
            if href.endswith(ext) or f'{ext}?' in href or f'{ext}#' in href:
                return True

    return False
def select_best_container_prefer_child(similar_containers, all_scored_containers):
    
    parent_child_pairs = []
    
    for i, container1 in enumerate(similar_containers):
        for j, container2 in enumerate(similar_containers):
            if i != j:
                if is_child_of(container2, container1):
                    score1 = next(score for c, score in all_scored_containers if c == container1)
                    score2 = next(score for c, score in all_scored_containers if c == container2)
                    parent_child_pairs.append((container1, container2, score1, score2))
    
    if parent_child_pairs:
        valid_children = []
        for parent, child, parent_score, child_score in parent_child_pairs:
            score_diff = parent_score - child_score
            if score_diff <= 20 and child_score >= 150:  
                valid_children.append((child, child_score, score_diff))
        
        if valid_children:
            valid_children.sort(key=lambda x: (-x[1], x[2]))  
            
            best_child, best_score, score_diff = valid_children[0]
            
            child_text_length = len(best_child.text_content().strip())
            parent_candidates = [parent for parent, child, p_score, c_score in parent_child_pairs 
                               if child == best_child]
            
            if parent_candidates:
                parent = parent_candidates[0]
                parent_text_length = len(parent.text_content().strip())
                
                if child_text_length < parent_text_length * 0.6:
                    return parent
                if has_document_attachments(parent):
                    return parent
            
            return best_child
    
    return select_deepest_container_from_similar(similar_containers)
def select_deepest_container_from_similar(similar_containers):
    if not similar_containers:
        return None
    
    if len(similar_containers) == 1:
        return similar_containers[0]
    
    container_depths = []
    for container in similar_containers:
        depth = calculate_container_depth(container)
        container_depths.append((container, depth))
    
    container_depths.sort(key=lambda x: x[1], reverse=True)
    
    deepest_container = container_depths[0][0]
    deepest_depth = container_depths[0][1]

    current = deepest_container
    for level in range(1, 4):  
        parent = current.getparent()
        if parent is None or parent.tag in ['body', 'html', None]:
            break

        if has_document_attachments(parent):
            if not has_document_attachments(deepest_container):
                return parent

        current = parent
    return deepest_container

def calculate_container_depth(container):
    depth = 0
    current = container
    
    while current is not None and current.tag not in ['body', 'html']:
        depth += 1
        current = current.getparent()
        if current is None:
            break
    
    return depth
def select_best_from_same_score_containers(containers):
    container_depths = []
    
    for container in containers:
        depth = calculate_container_depth(container)
        container_depths.append((container, depth))
        
    
    container_depths.sort(key=lambda x: x[1], reverse=True)
    
    best_container = container_depths[0][0]
    best_depth = container_depths[0][1]
    
    
    return best_container

def get_clean_text_content_lxml(container):
    if container is None:
        return ""

    from copy import deepcopy
    container_copy = deepcopy(container)

    for elem in container_copy.xpath('.//script | .//style'):
        elem.getparent().remove(elem)

    comments = container_copy.xpath('.//comment()')
    comment_count = 0
    for comment in comments:
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
            comment_count += 1

    clean_text = container_copy.text_content()
    return clean_text

def calculate_content_container_score(container):
    if container is None:
        return -1000
    score = 0
    debug_info = []

    classes = container.get('class', '').lower()
    elem_id = container.get('id', '').lower()

    text_content = get_clean_text_content_lxml(container)
    text_content_lower = text_content.lower()  
    text_length = len(text_content.strip())
    style = container.get('style', '').lower()
    if 'display' in style and 'none' in style:
        score -= 1000  
        return score
    
    current = container.getparent()
    depth = 0
    while current is not None and depth < 3:  
        parent_style = current.get('style', '').lower()
        if 'display' in parent_style and 'none' in parent_style:
            score -= 800  
            return score
        current = current.getparent()
        depth += 1
    sp1 = ['bszn-content']
    for keyword in sp1:
        if keyword.lower() in classes.lower():
            if 'bszn-content' in keyword.lower():
                score += 200 
            break

    sp2 = ['bszn-content']
    for keyword in sp2:
        if keyword.lower() in elem_id.lower():
            if 'bszn-content' in keyword.lower():
                score += 200 
            break

    special_class_keywords = ['tab-']
    for keyword in special_class_keywords:
        if keyword.lower() in classes.lower():
            if 'tab-' in keyword.lower():
                score -= 65 
            break
    special_id_keywords = ['tab-']
    for keyword in special_id_keywords:
        if keyword.lower() in elem_id.lower():
            if 'tab-' in keyword.lower():
                score -= 65  
            break
    
    if container.tag.lower() in ['header', 'footer', 'nav', 'aside','dropdown']:
        score -= 500  
        return score  
    
   
    strong_interference_keywords = [
        'header', 'footer', 'nav', 'navigation', 'menu', 'menubar', 'tab-',
        'topbar', 'bottom', 'sidebar', 'aside', 'banner', 'ad', 'advertisement','dropdown','drop'
    ]
    def is_valid_link(href):

        if not href or not isinstance(href, str):
            return False

        href = href.strip().lower()

        if not href or href == '#':
            return False

        if href.startswith('javascript:'):
            return False

        if href.startswith(('mailto:', 'tel:', 'sms:', 'data:', 'ftp:')):
            return False

        if 'void(' in href or 'return ' in href or 'function(' in href:
            return False

        return True
    def count_all_links(container):
        all_links = set()

        a_hrefs = container.xpath(".//a/@href")
        for href in a_hrefs:
            if is_valid_link(href):
                all_links.add(href)

        img_srcs = container.xpath(".//img/@src")
        all_links.update(img_srcs)

        data_srcs = container.xpath(".//@data-src")
        all_links.update(data_srcs)


        extracted_text = container.text_content()

        url_pattern = r'https?://[^\s<>"\']+(?:/\S*)?'
        text_urls = re.findall(url_pattern, extracted_text)
        relative_url_pattern = r'/[a-zA-Z0-9_/\\.-]+(?:/[a-zA-Z0-9_-]+)?\.(?:html?|php|jsp|asp|aspx|cgi|py)'
        relative_urls = re.findall(relative_url_pattern, extracted_text)
        all_extracted_urls = text_urls + relative_urls
        for url in all_extracted_urls:
            if url not in all_links:
                all_links.add(url)

        return len(all_links)
    def create_pattern(keyword):
        return re.compile(r'(^|[^\w-])' + re.escape(keyword) + r'([^\w-]|$)', re.IGNORECASE)

    interference_patterns = {kw: create_pattern(kw) for kw in strong_interference_keywords}

    interference_count = 0
    found_interference_keywords = []

    combined_text = f"{classes} {elem_id}".strip().lower()

    for keyword, pattern in interference_patterns.items():
        if pattern.search(combined_text):
            interference_count += 1
            found_interference_keywords.append(keyword)

    if interference_count > 0:
        interference_penalty = interference_count * 200
        score -= interference_penalty
        
        if interference_count >= 2:
            return score
    
    positive_content_keywords = [
        'content', 'article', 'main', 'body', 'text', 'detail', 
        'info', 'news', 'post', 'entry'
    ]
    
    positive_count = 0
    found_positive_keywords = []
    
    for keyword in positive_content_keywords:
        pattern = create_pattern(keyword)
        if pattern.search(combined_text):
            positive_count += 1
            found_positive_keywords.append(keyword)
    
    if positive_count > 0:
        positive_bonus = min(positive_count * 30, 90)
        score += positive_bonus

    header_content_keywords = [
        '登录', '注册', '首页', '主页', '无障碍',  '办事',   '无障碍浏览','打印','收藏','机构概况','在线服务','互动交流',
        '走进', '移动版', '手机版', '导航', '菜单', '搜索', '市政府',
        'login', 'register', 'home', 'menu', 'search', 'nav'
    ]
    
    footer_content_keywords = [
        '网站说明', '网站标识码', '版权所有', '主办单位', '承办单位', 
        '技术支持', '联系我们', '网站地图', '隐私政策', '免责声明',
        '备案号', 'icp', '公安备案', '政府网站', '网站管理','关闭窗口','打印文章','返回顶部',
        'copyright', 'all rights reserved', 'powered by', 'designed by','十字线','鼠标样式','读屏专用','ALT+Shift'
    ]
    
    found_header_keywords = [keyword for keyword in header_content_keywords if keyword in text_content_lower and not (('当前位置' in text_content_lower) or ('当前的位置' in  text_content_lower))]
    found_footer_keywords = [keyword for keyword in footer_content_keywords if keyword in text_content_lower]
    
    header_content_count = len(found_header_keywords)
    footer_content_count = len(found_footer_keywords)
    have_muchLinks = False
    link_count = count_all_links(container)
    all_links = container.xpath(".//a[@href]")
    valid_links_for_text = []
    for link in all_links:
        href = link.get('href', '').strip().lower()
        if (href and href != '#' and not href.startswith('javascript:')
            and not href.startswith(('mailto:', 'tel:', 'sms:', 'data:'))
            and 'void(' not in href):
            valid_links_for_text.append(link)

    if link_count and text_length > 0:
        link_text_total = sum(len(link.text_content().strip()) for link in valid_links_for_text)

        links_per_1000_chars = (link_count / text_length) * 1000
        link_text_ratio = link_text_total / text_length

        if link_count > 20:  
            score -= 200
        elif links_per_1000_chars > 10:  
            score -= 100
        elif link_text_ratio > 0.3:  
            score -= 50
         
        if link_count >= 5:
            have_muchLinks = True

    has_heading_tags = False
    try:
        heading_elements = container.xpath(".//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6")
        if heading_elements:
            has_heading_tags = True
    except:
        pass
    is_long_content = text_length > 3000
    
    if header_content_count >= 5:
        if is_long_content:
            if has_heading_tags:
                score -= 50
            else:
                score -= 100
        else:
            if has_heading_tags:
                score -= 150
            else:
                score -= 300
    elif header_content_count >= 3:
        if is_long_content:
            if has_heading_tags:
                score -= 0
            else:
                score -= 1
        else:
            if has_heading_tags:
                score -= 150
            else:
                score -= 300
    elif header_content_count >= 2:
        if is_long_content:
            if has_heading_tags:
                score -= 0
            else:
                score -= 1
        else:
            if has_heading_tags:
                score -= 75
            else:
                score -= 150
    
    if footer_content_count >= 3:
        if is_long_content:
            if has_heading_tags:
                score -= 50
            else:
                score -= 100
        else:
            if has_heading_tags:
                score -= 150
            else:
                score -= 300
    elif footer_content_count >= 2:
        if is_long_content:
            if has_heading_tags:
                score -= 0
            else:
                score -= 50
        else:
            if has_heading_tags:
                score -= 75
            else:
                score -= 150
    
    if score < -200 and not is_long_content:
        return score
    elif score < -200 and is_long_content:
        logger.info(f"⚠ 当前得分较低({score})，但文本较长({text_length}字符)，继续计算")
    
    if text_length > 5000 and not have_muchLinks:
        score+=200
    elif text_length > 1000:
        score += 50
    elif text_length > 500:
        score += 35
    elif text_length > 200:
        score += 20
    elif text_length < 50:
        score -= 20
    
    role = container.get('role', '').lower()
    if role == 'viewlist':
        score += 150
    elif role in ['list', 'listbox', 'grid', 'main', 'article']:
        score += 50
    
    content_indicators = [
        # 时间特征
        (r'\d{4}-\d{2}-\d{2}|\d{4}年\d{1,2}月\d{1,2}日|\d{4}/\d{1,2}/\d{1,2}|发布时间|更新日期|发布日期|成文日期', 30, '时间特征'),
        # 公文特征
        (r'通知|公告|意见|办法|规定|措施|方案|决定|指导|实施', 40, '公文特征'),
        # 条款特征
        (r'第[一二三四五六七八九十\d]+条|第[一二三四五六七八九十\d]+章|第[一二三四五六七八九十\d]+节', 35, '条款特征'),
        # 政务信息特征
        (r'索引号|主题分类|发文机关|发文字号|有效性', 25, '政务信息'),
        # 附件特征
        (r'附件|下载|pdf|doc|docx|文件下载', 20, '附件特征'),
        # 内容结构特征
        (r'为了|根据|按照|依据|现将|特制定|现印发|请结合实际', 30, '内容结构'),
        # 新闻内容特征
        (r'记者|报道|消息|新闻|采访|发表|刊登', 25, '新闻特征'),
        # 正文内容特征
        (r'正文|内容|详情|全文|摘要|概述', 20, '正文特征')
    ]
    
    total_content_score = 0
    matched_features = []
    
    for pattern, weight, feature_name in content_indicators:
        matches = re.findall(pattern, text_content_lower)  
        if matches:
            total_content_score += weight
            matched_features.append(f"{feature_name}({len(matches)})")
    
    if total_content_score > 0:
        final_content_score = min(total_content_score, 120)
        score += final_content_score
    else:
        logger.info(f"   ❌ 未发现内容特征")
    
    
    structured_elements = container.xpath(".//p | .//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6 | .//li | .//table | .//div[contains(@class,'content')] | .//section")
    if len(structured_elements) > 5:
        structure_score = min(len(structured_elements) * 2, 40)
        score += structure_score
    
    images = container.xpath(".//img")
    if len(images) > 0:
        image_score = min(len(images) * 3, 150)
        score += image_score
    
    
    container_info = f"{container.tag} class='{classes[:30]}'"
   
    
    return score

def exclude_page_header_footer(body):
    children = body.xpath("./div | ./main | ./section | ./article")
    
    if not children:
        return body
    
    valid_children = []
    for child in children:
        if not is_page_level_header_footer(child):
            valid_children.append(child)
    
    return find_middle_content(valid_children)

def is_page_level_header_footer(element):
    classes = element.get('class', '').lower()
    elem_id = element.get('id', '').lower()
    tag_name = element.tag.lower()
    
    if tag_name in ['header', 'footer', 'nav']:
        return True
    
    is_footer, _ = is_in_footer_area(element)
    if is_footer:
        return True
    
    page_keywords = ['header', 'footer', 'nav', 'menu', 'topbar', 'bottom', 'top','dropdown']
    for keyword in page_keywords:
        if keyword in classes or keyword in elem_id:
            return True
    
    role = element.get('role', '').lower()
    if role in ['banner', 'navigation', 'contentinfo']:
        return True
    
    return False

def find_middle_content(valid_children):
    if not valid_children:
        return None
    
    if len(valid_children) == 1:
        return valid_children[0]
    
    
    scored_containers = []
    for container in valid_children:
        score = calculate_content_richness(container)
        scored_containers.append((container, score))
    
    
    scored_containers.sort(key=lambda x: x[1], reverse=True)
    best_container = scored_containers[0][0]
    
    return best_container

def calculate_content_richness(container):
    score = 0

    text_content = get_clean_text_content_lxml(container).strip()
    content_length = len(text_content)
    
    if content_length > 1000:
        score += 40
    elif content_length > 500:
        score += 30
    elif content_length > 200:
        score += 20
    elif content_length > 100:
        score += 10
    else:
        return -5
    
    images = container.xpath(".//img")
    if len(images) > 0:
        score += min(len(images) * 3, 20)
    
    
    structured_elements = container.xpath(".//p | .//div[contains(@style, 'text-align')] | .//h1 | .//h2 | .//h3")
    if len(structured_elements) > 0:
        score += min(len(structured_elements) * 2, 25)
    
    return score

def exclude_local_header_footer(container):
    children = container.xpath("./div | ./section | ./article")
    
    if not children:
        return container
    
    valid_children = []
    for child in children:
        if not is_local_header_footer(child):
            valid_children.append(child)
    
    if not valid_children:
        return container
    
    return select_content_container(valid_children)

def is_local_header_footer(element):
    classes = element.get('class', '').lower()
    elem_id = element.get('id', '').lower()
    
    local_keywords = ['title', 'tit', 'head', 'foot', 'top', 'bottom', 'nav', 'menu','dropdown']
    for keyword in local_keywords:
        if keyword in classes or keyword in elem_id:
            text_content = element.text_content().strip()
            if len(text_content) < 200:  
                return True
    
    return False

def select_content_container(valid_children):
    if len(valid_children) == 1:
        return valid_children[0]
    
    scored_containers = []
    for container in valid_children:
        score = calculate_final_score(container)
        scored_containers.append((container, score))
    
    scored_containers.sort(key=lambda x: x[1], reverse=True)
    best_container = scored_containers[0][0]
    
    return best_container

def calculate_final_score(container):
    score = 0

    text_content = get_clean_text_content_lxml(container).strip()
    content_length = len(text_content)
    
    if content_length > 500:
        score += 30
    elif content_length > 200:
        score += 20
    elif content_length > 100:
        score += 15
    else:
        score += 5
    
    images = container.xpath(".//img")
    if len(images) > 0:
        score += min(len(images) * 4, 25)
    
    styled_divs = container.xpath(".//div[contains(@style, 'text-align')]")
    paragraphs = container.xpath(".//p")
    
    structure_count = len(styled_divs) + len(paragraphs)
    if structure_count > 0:
        score += min(structure_count * 2, 20)
    
    classes = container.get('class', '').lower()
    elem_id = container.get('id', '').lower()
    
    content_keywords = ['content', 'article', 'detail', 'main', 'body', 'text', 'editor', 'con']
    for keyword in content_keywords:
        if keyword in classes or keyword in elem_id:
            score += 15
    
    return score

def find_main_content_area(containers):
    candidates = []
    
    for container in containers:
        score = calculate_main_content_score(container)
        if score > 0:
            candidates.append((container, score))
    
    if not candidates:
        return None
    
    candidates.sort(key=lambda x: x[1], reverse=True)
    main_area = candidates[0][0]
    
    return main_area

def calculate_main_content_score(container):
    score = 0

    text_content = get_clean_text_content_lxml(container).strip()
    content_length = len(text_content)
    
    if content_length > 500:
        score += 30
    elif content_length > 200:
        score += 20
    elif content_length > 100:
        score += 10
    else:
        return -5  
    
    images = container.xpath(".//img")
    if len(images) > 0:
        score += min(len(images) * 2, 15)
    
    
    classes = container.get('class', '').lower()
    elem_id = container.get('id', '').lower()
    
    content_keywords = ['content', 'main', 'article', 'detail', 'body']
    for keyword in content_keywords:
        if keyword in classes or keyword in elem_id:
            score += 15
    classes = container.get('class', '').lower()
    if any(word in classes for word in ['content', 'article', 'detail', 'editor', 'text']):
        score += 15
    return score



def is_in_footer_area(element):
    """检查元素是否在footer区域"""
    current = element
    depth = 0
    while current is not None and depth < 10:  
        classes = current.get('class', '').lower()
        elem_id = current.get('id', '').lower()
        tag_name = current.tag.lower()
        
        footer_indicators = [
            'footer', 'bottom', 'foot', 'end', 'copyright', 
            'links', 'sitemap', 'contact', 'about'
        ]
        
        for indicator in footer_indicators:
            if (indicator in classes or indicator in elem_id or 
                (tag_name == 'footer')):
                return True, f"发现footer特征: {indicator} (第{depth}层)"
        
        style = current.get('style', '').lower()
        if 'bottom' in style or 'fixed' in style:
            return True, f"发现底部样式 (第{depth}层)"
        
        current = current.getparent()
        depth += 1
    
    return False, ""

def find_list_container(page_tree):
    article_container = find_article_container(page_tree)
    if article_container is not None:
        return article_container    
    list_selectors = [
        "//li", "//tr", "//article",
        "//div[contains(@class, 'item')]",
        "//div[contains(@class, 'list')]",
        "//ul//li", "//ol//li", "//table//tr",
        "//section//ul[contains(@class, 'item')]",
        "//section//ul[contains(@class, 'list')]",
        "//section//div[contains(@class, 'list')]",
        "//section//div[contains(@class, 'item')]"
    ]
    
    def count_list_items(element):
        items = element.xpath(".//li | .//tr | .//article | .//div[contains(@class, 'item')]")
        return len(items)
    
    def calculate_container_score(container):
        score = 0
        debug_info = []
        
        
        classes = container.get('class', '').lower()
        elem_id = container.get('id', '').lower()
        role = container.get('role', '').lower()
        tag_name = container.tag.lower()
        text_content = container.text_content().lower()
        
        header_content_keywords = [
            '登录', '注册', '首页', '主页', '无障碍', '办事', '无障碍浏览','打印','收藏','机构概况','在线服务','互动交流',
            '走进', '移动版', '手机版', '导航', '菜单', '搜索', '市政府',
            '长者模式','微信','ipv6','信息公开',
            'login', 'register', 'home', 'menu', 'search', 'nav'
        ]
        
        header_content_count = 0
        for keyword in header_content_keywords:
            if keyword in text_content:
                header_content_count += 1
        
        
        if header_content_count >= 2:
            score -= 300  
        
        footer_content_keywords = [
            '网站说明', '网站标识码', '版权所有', '主办单位', '承办单位', 
            '技术支持', '联系我们', '网站地图', '隐私政策', '免责声明',
            '备案号', 'icp', '公安备案', '政府网站', '网站管理',
            'copyright', 'all rights reserved', 'powered by', 'designed by'
        ]
        
        footer_content_count = 0
        for keyword in footer_content_keywords:
            if keyword in text_content:
                footer_content_count += 1
        
    
        if footer_content_count >= 2:
            score -= 300  
        
        footer_structure_indicators = ['footer', 'foot', 'bottom', 'end', 'copyright', 'links', 'sitemap']
        for indicator in footer_structure_indicators:
            if (indicator in classes or indicator in elem_id or 
                indicator in role or tag_name == 'footer'):
                score -= 250  
        
        header_structure_indicators = ['header', 'nav', 'navigation', 'menu', 'topbar', 'banner', 'menubar','dropdown']
        for indicator in header_structure_indicators:
            if (indicator in classes or indicator in elem_id or 
                indicator in role or tag_name in ['header', 'nav','menu']):
                score -= 200  
        
        current = container
        depth = 0
        while current is not None and depth < 5:  
            parent_classes = current.get('class', '').lower()
            parent_id = current.get('id', '').lower()
            parent_tag = current.tag.lower()
            
        
            for indicator in footer_structure_indicators:
                if (indicator in parent_classes or indicator in parent_id or parent_tag == 'footer'):
                    penalty = max(60 - depth * 10, 15)  # 
                    score -= penalty
            
            
            for indicator in header_structure_indicators:
                if (indicator in parent_classes or indicator in parent_id or parent_tag in ['header', 'nav']):
                    penalty = max(50 - depth * 8, 12)  # 
                    score -= penalty
            
            current = current.getparent()
            depth += 1
        
        if score < -150:
            return score
        
        precise_time_patterns = [
            r'\d{4}-\d{2}-\d{2}',  # --
            r'\d{4}年\d{1,2}月\d{1,2}日',  # 
            r'\d{4}/\d{1,2}/\d{1,2}',  # 
            r'发布时间', r'更新日期', r'发布日期', r'创建时间'
        ]
        
        precise_matches = 0
        for pattern in precise_time_patterns:
            matches = len(re.findall(pattern, text_content))
            precise_matches += matches
        
        if precise_matches > 0:
            time_score = min(precise_matches * 30, 90)  
            score += time_score
        
        
        items = container.xpath(".//*[self::li or self::tr or self::article or self::div[contains(@class, 'item')]]")
        if items:
            total_length = sum(len(item.text_content().strip()) for item in items)
            avg_length = total_length / len(items) if items else 0
            
            if avg_length > 150:
                score += 40  
            elif avg_length > 80:
                score += 30
            elif avg_length > 40:
                score += 20
            elif avg_length < 20:  
                score -= 20
        
        strong_positive_indicators = ['content', 'main', 'news', 'article', 'data', 'info', 'detail', 'result', 'list']
        positive_score = 0
        for indicator in strong_positive_indicators:
            if indicator in classes or indicator in elem_id:
                positive_score += 25  
        
        score += min(positive_score, 75)  
        
        
        images = container.xpath(".//img")
        links = container.xpath(".//a[@href]")
        
        if len(images) > 0:
            image_score = min(len(images) * 3, 20)
            score += image_score
        
        if len(links) > 5:  
            link_score = min(len(links) * 2, 30)
            score += link_score
        
        if items and len(items) > 2:
            strong_nav_words = [
                '登录', '注册', '首页', '主页', '无障碍', '办事', '无障碍浏览','打印','收藏','机构概况','在线服务','互动交流',
                '走进', '移动版', '手机版', '导航', '菜单', '搜索', '市政府',
                'login', 'register', 'home', 'menu', 'search', 'nav'
            ]
            nav_word_count = 0
            
            for item in items[:8]:  
                item_text = item.text_content().strip().lower()
                for nav_word in strong_nav_words:
                    if nav_word in item_text:
                        nav_word_count += 1
                        break
            
            checked_items = min(len(items), 8)
            if nav_word_count > checked_items * 0.4:  
                nav_penalty = 30  
                score -= nav_penalty
        
        container_info = f"标签:{tag_name}, 类名:{classes[:30]}{'...' if len(classes) > 30 else ''}"
        if elem_id:
            container_info += f", ID:{elem_id[:20]}{'...' if len(elem_id) > 20 else ''}"
        
        
        return score
    
    all_items = []
    for selector in list_selectors:
        items = page_tree.xpath(selector)
        all_items.extend(items)
    
    if not all_items:
        return None
    
    parent_counts = {}
    for item in all_items:
        parent = item.getparent()
        if parent is not None:
            if parent not in parent_counts:
                parent_counts[parent] = 0
            parent_counts[parent] += 1
    
    if not parent_counts:
        return None
    
    candidate_containers = [(parent, count) for parent, count in parent_counts.items() if count >= 3]
    
    if not candidate_containers:
        candidate_containers = [(parent, count) for parent, count in parent_counts.items() if count >= 2]
    
    if not candidate_containers:
        return max(parent_counts.items(), key=lambda x: x[1])[0]
    
    scored_containers = []
    for container, count in candidate_containers:
        score = calculate_container_score(container)
        
        is_footer, footer_msg = is_in_footer_area(container)
        ancestry_penalty = 0
        
        if is_footer:
            ancestry_penalty += 50  
        
        def check_negative_ancestry(element):
            penalty = 0
            current = element
            depth = 0
            while current is not None and depth < 4:  
                classes = current.get('class', '').lower()
                elem_id = current.get('id', '').lower()
                text_content = current.text_content().lower()
                
                negative_keywords = ['nav', 'menu', 'sidebar', 'header', 'topbar', 'navigation', 'head']
                for keyword in negative_keywords:
                    if keyword in classes or keyword in elem_id:
                        penalty += 20  
                
                
                if depth < 2:
                    footer_content_keywords = ['网站说明', '网站标识码', '版权所有', '备案号']
                    header_content_keywords = ['登录', '注册', '首页', '无障碍']
                    
                    content_penalty = 0
                    for keyword in footer_content_keywords + header_content_keywords:
                        if keyword in text_content:
                            content_penalty += 15
                    
                    if content_penalty > 30:  
                        penalty += content_penalty
                
                current = current.getparent()
                depth += 1
            return penalty
        
        ancestry_penalty += check_negative_ancestry(container)
        final_score = score - ancestry_penalty
        
        scored_containers.append((container, final_score, count))
    
    scored_containers.sort(key=lambda x: x[1], reverse=True)
    
    positive_scored = [sc for sc in scored_containers if sc[1] > 0]  
    
    if positive_scored:
        best_container = positive_scored[0][0]
        max_items = parent_counts[best_container]
    else:
        moderate_scored = [sc for sc in scored_containers if sc[1] > -50]
        
        if moderate_scored:
            best_container = moderate_scored[0][0]
            max_items = parent_counts[best_container]
        else:
            best_container = scored_containers[0][0]
            max_items = parent_counts[best_container]
    
    current_container = best_container
    while True:
        parent = current_container.getparent()
        if parent is None or parent.tag == 'html':
            break
        
        def has_negative_ancestor(element):
            current = element
            depth = 0
            while current is not None and depth < 3:  
                parent_classes = current.get('class', '').lower()
                parent_id = current.get('id', '').lower()
                parent_tag = current.tag.lower()
                parent_text = current.text_content().lower()
                
                structure_negative = ['footer', 'nav', 'menu', 'sidebar', 'header', 'topbar', 'navigation', 'foot', 'head']
                for keyword in structure_negative:
                    if (keyword in parent_classes or keyword in parent_id or parent_tag in ['footer', 'header', 'nav']):
                        return True
                
                if depth < 2:
                    
                    header_content = ['登录', '注册', '首页', '主页', '无障碍', '办事', '走进']
                    header_count = sum(1 for word in header_content if word in parent_text)
                    
                    footer_content = ['网站说明', '网站标识码', '版权所有', '备案号', 'icp', '主办单位', '承办单位']
                    footer_count = sum(1 for word in footer_content if word in parent_text)
                    
                    if header_count >= 2:
                        return True
                    if footer_count >= 2:
                        return True
                
                current = current.getparent()
                depth += 1
            return False
        
        if has_negative_ancestor(parent):
            break
            
        parent_items = count_list_items(parent)
        
        parent_score = calculate_container_score(parent)
        current_score = calculate_container_score(current_container)
        
        should_upgrade = False
        
        if parent_score < -50:
            logger.info(f"父级得分过低({parent_score})，跳过升级")
        else:
            if parent_score > current_score + 15 and parent_score > 10:
                should_upgrade = True
            
            elif (parent_score >= current_score - 3 and 
                  parent_score > 5 and  
                  parent_items <= max_items * 2 and  
                  parent_items >= max_items):
                should_upgrade = True
            
            elif (max_items < 4 and 
                  parent_items >= max_items and 
                  parent_items <= 15 and 
                  parent_score > 0):  
                should_upgrade = True
        
        if should_upgrade:
            current_container = parent
            max_items = parent_items
        else:
            break
        
        if parent_items > 50:
            break
    
    final_items = count_list_items(current_container)
    final_score = calculate_container_score(current_container)
    
    if final_items < 4 or final_score < -10:
        parent = current_container.getparent()
        if parent is not None and parent.tag != 'html':
            parent_items = count_list_items(parent)
            parent_score = calculate_container_score(parent)
            
            if (parent_items > final_items and 
                parent_score > 0 and  
                parent_items <= 30):  
                current_container = parent
            else:
                logger.info(f"父级不符合条件 (项目数: {parent_items}, 得分: {parent_score})，保持当前选择")
    
    return current_container
def generate_xpath(element):
    if element is None:
        return None

    tag = element.tag

    elem_id = element.get('id')
    if elem_id and not is_interference_identifier(elem_id):
        return f"//{tag}[@id='{elem_id}']"

    classes = element.get('class')
    if classes:
        return f"//{tag}[@class='{classes}']"

    for attr in ['aria-label', 'role', 'data-testid', 'data-role']:
        attr_value = element.get(attr)
        if attr_value and not is_interference_identifier(attr_value):
            return f"//{tag}[@{attr}='{attr_value}']"

    def find_closest_clean_identifier(el):
        parent = el.getparent()
        while parent is not None and parent.tag != 'html':
            parent_id = parent.get('id')
            if parent_id and not is_interference_identifier(parent_id):
                return parent
            
            parent_classes = parent.get('class')
            if parent_classes:
                parent_class_list = [cls.strip() for cls in parent_classes.split() if cls.strip()]
                clean_parent_classes = [cls for cls in parent_class_list if not is_interference_identifier(cls)]
                if clean_parent_classes:
                    return parent
            parent = parent.getparent()
        return None

    ancestor = find_closest_clean_identifier(element)
    if ancestor is not None:
        ancestor_xpath = generate_xpath(ancestor)
        if ancestor_xpath:
            def generate_relative_path(ancestor_el, target_el):
                path = []
                current = target_el
                while current is not None and current != ancestor_el:
                    index = 1
                    sibling = current.getprevious()
                    while sibling is not None:
                        if sibling.tag == current.tag:
                            index += 1
                        sibling = sibling.getprevious()
                    path.insert(0, f"{current.tag}[{index}]")
                    current = current.getparent()
                return '/' + '/'.join(path)

            relative_path = generate_relative_path(ancestor, element)
            return f"{ancestor_xpath}{relative_path}"

    path = []
    current = element
    while current is not None and current.tag != 'html':
        index = 1
        sibling = current.getprevious()
        while sibling is not None:
            if sibling.tag == current.tag:
                index += 1
            sibling = sibling.getprevious()
        path.insert(0, f"{current.tag}[{index}]")
        current = current.getparent()

    return '/' + '/'.join(path)

def is_interference_identifier(identifier):
    if not identifier:
        return False
    
    identifier_lower = identifier.lower()
    
    
    interference_keywords = [
        'header', 'footer', 'nav', 'navigation', 'menu', 'menubar',
        'topbar', 'bottom', 'sidebar', 'aside', 'banner', 'ad'
    ]
    
    for keyword in interference_keywords:
        if keyword in identifier_lower:
            return True
    
    return False
def clean_html_content_advanced_two(html_content: str) -> str:
   
    try:
        soup = BeautifulSoup(html_content, 'html.parser')

         
        for tag in soup.find_all(['script', 'style', 'meta', 'link', 'noscript']):
            tag.decompose()

        
        tags_to_delete = [
            "已阅","字号", "打印", "关闭", "收藏","分享到微信","分享","字体","小","中","大","s92及gd格式的文件请用SEP阅读工具",
            "扫一扫在手机打开当前页", "扫一扫在手机上查看当前页面","用微信“扫一扫”","分享给您的微信好友",
            "相关链接",'下载文字版','下载图片版','扫一扫在手机打开当前页面',"微信扫一扫：分享","上一篇","下一篇","【打印文章】","返回顶部","你的浏览器不支持video",
            "当前位置：","首页","信息公开目录","索引号","发布时间：202"
        ]

        for tag_text in tags_to_delete:
            delete_short_tags(soup, tag_text)

        error_elements_to_delete = []

        for element in soup.find_all(string=re.compile("我要纠错")):
            if not hasattr(element, 'parent') or element.parent is None:
                continue

            parent = element.parent

            if not hasattr(parent, 'get_text') or not hasattr(parent, 'decompose'):
                continue

            try:
                if parent and len(parent.get_text(strip=True)) < 20:  # 
                    error_elements_to_delete.append(parent)
            except Exception:
                 
                continue

         
        for parent in error_elements_to_delete:
            try:
                if parent and hasattr(parent, 'decompose'):
                    parent.decompose()
            except Exception:
                pass

        
        essential_attributes = {
            'div': [], 'p': [], 'span': [],
            'table': ['border', 'cellpadding', 'cellspacing'],
            'tr': [], 'td': ['colspan', 'rowspan'], 'th': ['colspan', 'rowspan'],
            'ul': [], 'ol': [], 'li': [],
            'a': ['href', 'target'],
            'img': ['src'],
            'video': ['src', 'poster', 'controls'],
            'source': ['src'],
            'iframe': ['src'],
            'br': [], 'hr': [],
            'button':['path']
        }

        def clean_attributes(tag):
            if tag.name is None:
                return

            allowed_attrs = essential_attributes.get(tag.name, [])

               
            if tag.has_attr('style'):
                del tag['style']

            attrs_to_remove = [attr for attr in tag.attrs if attr not in allowed_attrs]
            for attr in attrs_to_remove:
                del tag[attr]

        for tag in soup.find_all(True):
            clean_attributes(tag)

        for img in soup.find_all('img'):
            if img:
                src = img.get('src','')
                if not src or 'base64' in src.lower() or 'data:image' in src.lower():
                    img.decompose()         
        for table in soup.find_all('table'):
            cleaned_table = clean_table_html(str(table))
            table.replace_with(BeautifulSoup(cleaned_table, 'html.parser'))

         
        remove_empty_tags(soup)

        return str(soup)

    except Exception as e:
        return html_content

import json 


def fix_relative_links_in_html(html_content: str, base_url: str) -> str:
 
    if not html_content or not base_url:
        return html_content

    try:
        from bs4 import BeautifulSoup
        import urllib.parse

        processed_base_url = process_base_url(base_url)

        soup = BeautifulSoup(html_content, 'html.parser')

        def is_relative_path(url: str) -> bool:
            if not url:
                return False
            if url.startswith('/'):
                return True
            if '://' not in url and not url.startswith(('javascript:', 'mailto:', 'tel:', '#', 'data:')):
                return True
            return False

        for tag in soup.find_all('a', href=True):
            href = tag['href']
            if is_relative_path(href):
                tag['href'] = urllib.parse.urljoin(processed_base_url, href)

        for tag in soup.find_all('img', src=True):
            src = tag['src']
            if is_relative_path(src):
                tag['src'] = urllib.parse.urljoin(processed_base_url, src)

        for tag in soup.find_all('video'):
            if tag.get('src') and is_relative_path(tag['src']):
                tag['src'] = urllib.parse.urljoin(processed_base_url, tag['src'])
            if tag.get('poster') and is_relative_path(tag['poster']):
                tag['poster'] = urllib.parse.urljoin(processed_base_url, tag['poster'])

        for tag in soup.find_all('audio', src=True):
            src = tag['src']
            if is_relative_path(src):
                tag['src'] = urllib.parse.urljoin(processed_base_url, src)

        for tag in soup.find_all('source', src=True):
            src = tag['src']
            if is_relative_path(src):
                tag['src'] = urllib.parse.urljoin(processed_base_url, src)

        for tag in soup.find_all('iframe', src=True):
            src = tag['src']
            if is_relative_path(src):
                tag['src'] = urllib.parse.urljoin(processed_base_url, src)

        for tag in soup.find_all('link', href=True):
            href = tag['href']
            if is_relative_path(href):
                tag['href'] = urllib.parse.urljoin(processed_base_url, href)

        for tag in soup.find_all('script', src=True):
            src = tag['src']
            if is_relative_path(src):
                tag['src'] = urllib.parse.urljoin(processed_base_url, src)

        return str(soup)

    except Exception as e:
        logger.error(f"处理HTML相对链接时出错: {e}")
        return html_content

def progressResult(json_str: dict, url: str = "") -> dict:
    
    try:
        markdown_content = json_str.get("markdown_content", '')
        html_contents = json_str.get("html_content", '')
        xpath = json_str.get('xpath', '')
        elapsed = json_str.get('elapsed', 0)
        if url and html_contents:
            html_contents = fix_relative_links_in_html(html_contents, url)
        result = {
            'markdown_content': markdown_content,
            'html_content': html_contents,
            'xpath': xpath,
            'elapsed': elapsed,
            'header_content_text': '',  
            'cl_content_html': '',      
            'cl_content_md': '',        
            'cl_content_text': '',      
            'extract_success': False
        }

        if not html_contents.strip():
            return result

        header_content_text, cl_content_html, cl_content_md, cl_content_text = clean_html_content_with_split(html_contents)

        extract_success = bool(
            cl_content_md.strip() and
            len(cl_content_md) > 150
        )

        if not extract_success:
            cl_content_html = clean_html_content_advanced_two(html_contents)
            cl_content_md = html_to_markdown_simple(cl_content_html)
            content_soup = BeautifulSoup(cl_content_html, 'html.parser')
            cl_content_text = clean_text(content_soup.get_text())
            extract_success = bool(
                cl_content_md.strip() and
                len(cl_content_md) > 150
            )

        
        result.update({
            'header_content_text': header_content_text,  
            'cl_content_html': cl_content_html,           
            'cl_content_md': cl_content_md,              
            'cl_content_text': cl_content_text,          
            'extract_success': extract_success
        })

        return result

    except Exception as e:
        import traceback

        try:
            markdown_content = json_str.get('markdown_content', '') if isinstance(json_str, dict) else ''
            html_content = json_str.get('html_content', '') if isinstance(json_str, dict) else ''
            xpath = json_str.get('xpath', '') if isinstance(json_str, dict) else ''
            elapsed = json_str.get('elapsed', 0) if isinstance(json_str, dict) else 0
        except:
            markdown_content = ''
            html_content = ''
            xpath = ''
            elapsed = 0

        return {
            'markdown_content': markdown_content,
            'html_content': html_content,
            'xpath': xpath,
            'elapsed': elapsed,
            'header_content_text': '',  
            'cl_content_html': '',       
            'cl_content_md': '',         
            'extract_success': False
        }


def clean_footer_content(html_content: str) -> str:
    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        footer_patterns = [
            r'网站说明',
            r'网站标识码',
            r'版权所有',
            r'主办单位',
            r'承办单位',
            r'技术支持',
            r'联系我们',
            r'网站地图',
            r'隐私政策',
            r'免责声明',
            r'备案号',
            r'icp备案',
            r'公安备案',
            r'政府网站',
            r'网站管理',
            r'copyright',
            r'all rights reserved',
            r'powered by',
            r'打印',
            r'分享',
            r'收藏',
            r'扫一扫'
        ]

        elements_to_remove = []
        for element in soup.find_all(True):
            text = element.get_text().strip().lower()
            for pattern in footer_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    elements_to_remove.append(element)
                    break

        for element in elements_to_remove:
            element.decompose()

        return str(soup)

    except Exception as e:
        return html_content


def html_to_markdown_simple(html_content: str) -> str:
    try:
        if not html_content.strip():
            return ''

        converter = CustomMarkdownConverter(
            heading_style="ATX",
            bullets="*",
            strip=['script', 'style']
        )

        markdown_content = converter.convert(html_content)

        markdown_content = clean_markdown_content(markdown_content)

        return markdown_content.strip()

    except Exception as e:
        return ''



IGNORED_QUERY_PARAMS: Set[str] = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'ref', '_t', 'timestamp', 'v', 'cache_bust'
}

def normalize_url(url: str, ignore_params: Set[str] = IGNORED_QUERY_PARAMS) -> str:
    """
    对URL进行标准化，用于去重和哈希。
    """
    parsed = urllib.parse.urlparse(url)
    query_dict = parse_qs(parsed.query, keep_blank_values=True)

    if ignore_params:
        query_dict = {k: v for k, v in query_dict.items() if k not in ignore_params}

    sorted_items = []
    for key in sorted(query_dict.keys()):
        values = query_dict[key]
        for val in values:
            sorted_items.append((key, val))

    normalized_query = urlencode(
        sorted_items,
        doseq=False,
        safe='',
        quote_via=lambda x, *args, **kwargs: x
    )

    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc.lower(),
        parsed.path,
        parsed.params,
        normalized_query,
        ''
    ))

    return normalized


class URLPlaceholderReplacer:

    def __init__(self):
        self.placeholder_mapping = {}

    def is_media_url(self, url: str) -> bool:
        if not url:
            return False

        video_extensions = ['.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv', '.m4v']
        audio_extensions = ['.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a']
        file_extensions = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                         '.zip', '.rar', '.tar', '.gz', '.7z', '.txt', '.rtf']
        pic_extensions = ['.jpg', '.png', '.jpeg', '.webp', '.svg']
        url_lower = url.lower()
        
        for ext in video_extensions + audio_extensions + file_extensions + pic_extensions:
            if url_lower.endswith(ext):
                return True

        media_keywords = ['video', 'audio', 'player', 'stream', 'media', 'download', 'file']
        for keyword in media_keywords:
            if keyword in url_lower:
                return True

        return False

    def _generate_placeholder(self, url: str) -> str:
        normalized_url = normalize_url(url)
        url_hash = hashlib.sha256(normalized_url.encode('utf-8')).hexdigest()
        md5content = url_hash[:32]
        prefix = url_hash[:3]
        placeholder = f"{prefix}/{md5content}"
        self.placeholder_mapping[placeholder] = url
        return placeholder

    def replace_urls_with_placeholders(self, html_content: str, base_url: str = "") -> str:
        soup = BeautifulSoup(html_content, 'html.parser')
        def is_relative_path(url:str)->bool:
            if not url:
                return False
            if url.startswith("/"):
                return True
            if '://' not in url and not url.startswith(('javascript:', 'mailto:', 'tel:', '#', 'data:')):
                return True
            return False
        def process_url(url: str) -> str:
            if not url:
                return ""
            if is_relative_path(url) and base_url:
                url = urllib.parse.urljoin(base_url, url)
            return url

        def should_skip_url(url: str) -> bool:
            if not url:
                return False
            url_lower = url.lower().split("?")[0]
            return url_lower.endswith('.html') or url_lower.endswith(".htm")

        for video in soup.find_all('video'):
            src = video.get('src')
            if src:
                full_src = process_url(src)
                if not should_skip_url(full_src):
                    placeholder = self._generate_placeholder(full_src)
                    self.placeholder_mapping[placeholder] = full_src
                    video['src'] = f"{{{{{placeholder}}}}}"

            poster = video.get('poster')
            if poster:
                full_poster = process_url(poster)
                if not should_skip_url(full_poster):
                    placeholder = self._generate_placeholder(full_poster)
                    self.placeholder_mapping[placeholder] = full_poster
                    video['poster'] = f"{{{{{placeholder}}}}}"

        for source in soup.find_all('source'):
            src = source.get('src')
            if src:
                full_src = process_url(src)
                if not should_skip_url(full_src):
                    placeholder = self._generate_placeholder(full_src)
                    self.placeholder_mapping[placeholder] = full_src
                    source['src'] = f"{{{{{placeholder}}}}}"

        for audio in soup.find_all('audio'):
            src = audio.get('src')
            if src:
                full_src = process_url(src)
                if not should_skip_url(full_src):
                    placeholder = self._generate_placeholder(full_src)
                    self.placeholder_mapping[placeholder] = full_src
                    audio['src'] = f"{{{{{placeholder}}}}}"

        for iframe in soup.find_all('iframe'):
            src = iframe.get('src')
            if src and ('player' in src.lower() or 'video' in src.lower() or 'audio' in src.lower()):
                full_src = process_url(src)
                if not should_skip_url(full_src):
                    placeholder = self._generate_placeholder(full_src)
                    self.placeholder_mapping[placeholder] = full_src
                    iframe['src'] = f"{{{{{placeholder}}}}}"

        for button in soup.find_all('button'):
            src = button.get('path')
            if src:
                src_lower = src.lower()
                full_src = process_url(src)

                audio_extensions = ('.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac')

                if any(ext in src_lower for ext in audio_extensions):
                    if not should_skip_url(full_src):
                        placeholder = self._generate_placeholder(full_src)
                        self.placeholder_mapping[placeholder] = full_src
                        audio_tag = soup.new_tag('audio')
                        audio_tag['src'] = f"{{{{{placeholder}}}}}"
                        audio_tag['controls'] = 'controls'
                        button.replace_with(audio_tag)

        for img in soup.find_all('img'):
            src = img.get('src')
            if src:
                full_src = process_url(src)
                if not should_skip_url(full_src):
                    placeholder = self._generate_placeholder(full_src)
                    self.placeholder_mapping[placeholder] = full_src
                    img['src'] = f"{{{{{placeholder}}}}}"

        for a in soup.find_all('a'):
            href = a.get('href')
            if href:
                full_href = href
                if is_relative_path(href) and base_url:
                    full_href = urllib.parse.urljoin(base_url, href)
                    a['href'] = full_href

                if self.is_media_url(full_href):
                    if not should_skip_url(full_href):
                        placeholder = self._generate_placeholder(full_href)
                        self.placeholder_mapping[placeholder] = full_href
                        a['href'] = f"{{{{{placeholder}}}}}"
        return str(soup)


def process_base_url(url):
    if not url:
        return ""

    try:
        from urllib.parse import urlparse
        url_obj = urlparse(url)
        pathname = url_obj.path

        last_slash_index = pathname.rfind('/')
        if last_slash_index > 0:
            url_obj = url_obj._replace(path=pathname[:last_slash_index + 1])
        elif pathname == '/':
            pass
        else:
            url_obj = url_obj._replace(path='/')

        return url_obj.geturl()
    except Exception as e:
        return url


def html_to_text(html_content: str) -> str:
    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        for script in soup(["script", "style"]):
            script.decompose()

        text = soup.get_text()

        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        return '\n'.join(chunk for chunk in chunks if chunk)

    except Exception as e:
        return re.sub(r'<[^>]+>', '', html_content)


def process_with_placeholders(html_content: str, url: str = "") -> dict:
    if url:
        base_url = process_base_url(url)
    else:
        base_url = ""

    replacer = URLPlaceholderReplacer()
    html_with_placeholders = replacer.replace_urls_with_placeholders(html_content, base_url)

    converter = CustomMarkdownConverter(
        heading_style="ATX",
        bullets="*",
        strip=['script', 'style']
    )
    md_with_placeholders = converter.convert(html_with_placeholders)
    md_with_placeholders = clean_markdown_content(md_with_placeholders)

    text_with_placeholders = html_to_text(html_with_placeholders)

    placeholder_mapping_list = [{"placeholder": k, "original_url": v}
                                for k, v in replacer.placeholder_mapping.items()]
    placeholder_mapping = json.dumps(placeholder_mapping_list, ensure_ascii=False, indent=2)

    return {
        "placeholder_html": html_with_placeholders,
        "placeholder_markdown": md_with_placeholders,
        "placeholder_text": text_with_placeholders,
        "placeholder_mapping": placeholder_mapping
    }

@app.get("/")
async def root():
    return {
        "message": "HTML to Markdown Content Extractor API",
        "version": "2.0.0",
        "endpoints": {
            "/extract": "POST - Extract main content from HTML and convert to Markdown",
            "/convert_to_markdown": "POST - Convert HTML to Markdown with placeholder replacement (no content extraction)",
            "/health": "GET - Health check"
        }
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }
import time
@app.post("/extract", response_model=MarkdownOutput)
async def extract_html_to_markdown(input_data: HTMLInput):

    try:
        if not input_data.html_content.strip():
            raise HTTPException(status_code=400, detail="HTML内容不能为空")

        start_time = time.time()

        if input_data.xpath and input_data.xpath.strip():

            html_content = re.sub(r'<!--[\s\S]*?-->', '', input_data.html_content)

            tree = lxml_html.fromstring(html_content)

            elements = tree.xpath(input_data.xpath.strip())

            if not elements:
                raise HTTPException(status_code=422, detail=f"xpath未找到任何元素: {input_data.xpath}")

            main_container = elements[0]

            container_html = lxml_html.tostring(main_container, encoding='unicode', pretty_print=True)

            cleaned_content_html = clean_html_content_advanced(container_html)

            content_md = html_to_markdown_simple(cleaned_content_html)

            content_soup = BeautifulSoup(cleaned_content_html, 'html.parser')
            content_text = clean_text(content_soup.get_text())

            final_result = {
                'html_content': container_html,
                'xpath': input_data.xpath.strip(),
                'status': 'success',
                'header_content_text': '',  
                'cl_content_html': cleaned_content_html,
                'cl_content_md': content_md,
                'cl_content_text': content_text,
                'extract_success': True
            }

        else:
            result = extract_content_to_markdown(input_data.html_content)

            if result['status'] == 'failed':
                raise HTTPException(status_code=422, detail="无法从HTML中提取有效内容")

            final_result = progressResult(result, input_data.url)

        end_time = time.time()
        elapsed = end_time - start_time

        placeholder_html = ""
        placeholder_markdown = ""
        placeholder_text = ""
        placeholder_mapping = ""

        if input_data.need_placeholder:
            html_for_placeholder = final_result.get('cl_content_html', '')
            if html_for_placeholder:
                placeholder_result = process_with_placeholders(html_for_placeholder, input_data.url)
                placeholder_html = placeholder_result.get('placeholder_html', '')
                placeholder_markdown = placeholder_result.get('placeholder_markdown', '')
                placeholder_mapping = placeholder_result.get('placeholder_mapping', '')

        return MarkdownOutput(
            html_content=final_result.get('html_content', ''),
            status=final_result.get('status', 'success'),
            header_content_text=final_result.get('header_content_text', ''),
            cl_content_html=final_result.get('cl_content_html', ''),
            cl_content_md=final_result.get('cl_content_md', ''),
            content_text=final_result.get('cl_content_text', ''),
            extract_success=final_result.get('extract_success', False),
            placeholder_html=placeholder_html,
            placeholder_markdown=placeholder_markdown,
            placeholder_mapping=placeholder_mapping
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

@app.post("/convert_to_markdown", response_model=SimpleMarkdownOutput)
async def convert_html_to_markdown(input_data: SimpleMarkdownInput):
    try:
        if not input_data.html_content.strip():
            raise HTTPException(status_code=400, detail="HTML内容不能为空")

        cleaned_html = clean_html_content_advanced(input_data.html_content)

        placeholder_result = process_with_placeholders(cleaned_html, input_data.url)

        return SimpleMarkdownOutput(
            success=True,
            placeholder_markdown=placeholder_result.get('placeholder_markdown', ''),
            placeholder_mapping=placeholder_result.get('placeholder_mapping', '')
        )

    except HTTPException:
        raise
    except Exception as e:
        return SimpleMarkdownOutput(
            success=False,
            placeholder_markdown="",
            placeholder_mapping=""
        )

import os
import glob

def start_server(host: str = "0.0.0.0", port: int = 8101):
    uvicorn.run(app, host=host, port=port,log_level="critical",access_log=False)
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "api":
        port = int(os.getenv("PORT", 8101))
        start_server(port=port)
