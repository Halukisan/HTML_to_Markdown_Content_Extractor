import re
import logging
from datetime import datetime
from lxml import html
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import markdownify
import uvicorn

# 配置日志 - 高并发优化版本
def setup_logging():
    """设置日志配置 - 减少IO开销"""
    # 生产环境只记录WARNING及以上级别
    log_level = logging.WARNING  # 从INFO改为WARNING
    
    # 配置日志格式（简化格式）
    logging.basicConfig(
        level=log_level,
        format='%(levelname)s - %(message)s',  # 简化格式
        handlers=[
            logging.StreamHandler()  # 只输出到控制台，减少文件IO
        ]
    )
    
    return logging.getLogger(__name__)

# 初始化日志
logger = setup_logging()

# FastAPI应用
app = FastAPI(
    title="HTML to Markdown Content Extractor",
    description="Extract main content from HTML and convert to Markdown",
    version="2.0.0"
)

# Pydantic模型
class HTMLInput(BaseModel):
    html_content: str
    url: str = ""  # 可选的URL字段，暂时不处理
    
class MarkdownOutput(BaseModel):
    markdown_content: str
    html_content: str  # 新增：提取的HTML内容
    xpath: str
    status: str

# 移除了浏览器相关的函数，现在只处理HTML内容
def remove_header_footer_by_content_traceback(body):
    
    # 首部内容特征关键词
    header_content_keywords = [
        '登录', '注册', '首页', '主页', '无障碍', '办事', '无障碍浏览','打印','收藏','机构概况','在线服务','互动交流',
        '走进', '移动版', '手机版', '导航', '菜单', '搜索', '市政府',
        'login', 'register', 'home', 'menu', 'search', 'nav'
    ]
    
    # 尾部内容特征关键词
    footer_content_keywords = [
        '网站说明', '网站标识码', '版权所有', '主办单位', '承办单位', 
        '技术支持', '联系我们', '网站地图', '隐私政策', '免责声明',
        '备案号', 'icp', '公安备案', '政府网站', '网站管理',
        'copyright', 'all rights reserved', 'powered by', 'designed by'
    ]
    
    # 查找包含首部特征文字的元素
    header_elements = []
    for keyword in header_content_keywords:
        xpath = f"//*[contains(text(), '{keyword}')]"
        elements = body.xpath(xpath)
        header_elements.extend(elements)
    
    # 查找包含尾部特征文字的元素
    footer_elements = []
    for keyword in footer_content_keywords:
        xpath = f"//*[contains(text(), '{keyword}')]"
        elements = body.xpath(xpath)
        footer_elements.extend(elements)
    
    # 收集需要删除的容器
    containers_to_remove = set()
    
    # 处理首部元素
    for element in header_elements:
        container = find_header_footer_container(element)
        if container and container not in containers_to_remove:
            containers_to_remove.add(container)
            logger.info(f"发现首部容器: {container.tag} class='{container.get('class', '')[:50]}'")
    
    # 处理尾部元素
    for element in footer_elements:
        container = find_footer_container_by_traceback(element)
        if container and container not in containers_to_remove:
            containers_to_remove.add(container)
            logger.info(f"发现尾部容器: {container.tag} class='{container.get('class', '')[:50]}'")
    
    # 额外检查：查找所有直接包含header/footer标签的div容器
    header_divs = body.xpath(".//div[.//header] | .//div[.//footer] | .//div[.//nav]")
    for div in header_divs:
        # 检查这个div是否包含首部/尾部内容特征
        div_text = div.text_content().lower()
        
        header_count = sum(1 for keyword in header_content_keywords if keyword in div_text)
        footer_count = sum(1 for keyword in footer_content_keywords if keyword in div_text)
        
        if header_count >= 2 or footer_count >= 2:
            if div not in containers_to_remove:
                containers_to_remove.add(div)    
    # 删除容器
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
    """通过回溯找到包含首部/尾部特征的容器 - 增强版"""
    current = element
    
    # 向上回溯查找容器
    while current is not None and current.tag != 'html':
        # 检查当前元素是否为容器（div、section、header、footer、nav等）
        if current.tag in ['div', 'section', 'header', 'footer', 'nav', 'aside']:
            # 检查容器是否包含首部/尾部结构特征
            classes = current.get('class', '').lower()
            elem_id = current.get('id', '').lower()
            tag_name = current.tag.lower()
            
            # 首部结构特征
            header_indicators = ['header', 'nav', 'navigation', 'menu', 'topbar', 'banner', 'menubar', 'head']
            # 尾部结构特征
            footer_indicators = ['footer', 'foot', 'bottom', 'end', 'copyright', 'links', 'sitemap', 'contact']
            
            # 检查是否包含首部或尾部结构特征
            for indicator in header_indicators + footer_indicators:
                if (indicator in classes or indicator in elem_id or indicator in tag_name):
                    return current
        
        # 检查是否到达顶层标签
        parent = current.getparent()
        if parent is None or parent.tag in ['html', 'head', 'body', 'script', 'meta']:
            # 如果父级是html或body，说明已经到顶了
            break
        
        # 继续向上查找
        current = parent
    
    # 特殊处理：如果当前元素被div包装，但div本身没有明显特征
    # 检查当前元素的父级是否是div，且祖父级是body/html
    if (element.getparent() and 
        element.getparent().tag == 'div' and 
        element.getparent().getparent() and 
        element.getparent().getparent().tag in ['body', 'html']):
        
        # 检查这个div是否包含首部/尾部内容特征
        div_element = element.getparent()
        div_text = div_element.text_content().lower()
        
        # 首部内容特征关键词
        header_content_keywords = [
            '登录', '注册', '首页', '主页', '无障碍',  '办事',  '无障碍浏览','打印','收藏','机构概况','在线服务','互动交流', 
            '走进', '移动版', '手机版', '导航', '菜单', '搜索', '市政府'
        ]
        
        # 尾部内容特征关键词
        footer_content_keywords = [
            '网站说明', '网站标识码', '版权所有', '主办单位', '承办单位', 
            '技术支持', '联系我们', '网站地图', '隐私政策', '免责声明',
            '备案号', 'icp', '公安备案', '政府网站', '网站管理'
        ]
        
        # 检查是否包含多个首部或尾部关键词
        header_count = sum(1 for keyword in header_content_keywords if keyword in div_text)
        footer_count = sum(1 for keyword in footer_content_keywords if keyword in div_text)
        
        if header_count >= 2 or footer_count >= 2:
            return div_element
    
    # 如果没有找到明显的结构特征容器，返回直接父级容器
    if element.getparent() and element.getparent().tag != 'html':
        return element.getparent()
    
    return None
def find_footer_container_by_traceback(element):
    """通过回溯找到footer容器"""
    current = element
    
    while current is not None:
        # 检查当前元素是否为容器
        if current.tag in ['div', 'section', 'footer']:
            # 检查容器特征
            classes = current.get('class', '').lower()
            elem_id = current.get('id', '').lower()
            
            # footer结构特征
            footer_indicators = ['footer', 'foot', 'bottom', 'end', 'copyright']
            for indicator in footer_indicators:
                if indicator in classes or indicator in elem_id:
                    return current
        
        # 检查是否到达顶层标签
        parent = current.getparent()
        if parent is None or parent.tag in ['html', 'head', 'body', 'script', 'meta']:
            break
            
        current = parent
    
    return None
def preprocess_html_remove_interference(page_tree):
    """
    精准清理HTML - 只激进删除页面级header和footer，保护内容区域
    """
    # 获取body元素
    body_elements = page_tree.xpath("//body")
    if body_elements:
        body = body_elements[0]
    else:
        # 如果没有body标签，尝试使用整个树
        body = page_tree
        logger.warning("未找到body标签，使用整个HTML树")
    
    if body is None:
        logger.error("HTML解析失败，body为None")
        return None
    
    logger.info("开始精准HTML清理流程...")
    
    # 第零步：删除所有 display:none 的不可见元素 以及 class为ng-hide的元素（ng-hide为前端框架中固定的class名称）
    display_none_count = remove_display_none_elements(body)
    logger.info(f"删除了 {display_none_count} 个 display:none 不可见元素")
    
    # 第一步：激进删除明确的页面级header和footer
    removed_count = remove_page_level_header_footer(body)
    
    logger.info(f"精准清理完成：删除了 {removed_count} 个页面级header/footer")
    
    # 输出清理后的HTML到日志文件
    cleaned_html = html.tostring(body, encoding='unicode', pretty_print=True)
    logger.info("\n=== 清理后的HTML内容(只展示前2000字) ===")
    logger.info(cleaned_html[:2000] + "..." if len(cleaned_html) > 2000 else cleaned_html)
    logger.info("=== HTML内容结束 ===\n")
    
    return body

def remove_display_none_elements(body):
    """
    删除所有 display:none 的不可见元素及其子元素
    以及删除class为ng-hide的元素及其子元素
    这些元素在页面上不可见，不应该被提取
    """
    logger.info("开始删除 display:none 不可见元素和 ng-hide 元素...")
    
    removed_count = 0
    
    # 查找所有有 style 属性的元素
    elements_with_style = body.xpath(".//*[@style]")
    
    # 查找所有有 ng-hide class 的元素
    ng_hide_elements = body.xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' ng-hide ')]")
    
    elements_to_remove = []
    
    # 处理 display:none 元素
    for element in elements_with_style:
        style = element.get('style', '').lower()
        
        # 检查是否包含 display:none 或 display: none
        if 'display' in style and 'none' in style:
            # 更精确的检查，避免误判（如 display:inline）
            import re
            if re.search(r'display\s*:\s*none', style, re.IGNORECASE):
                elements_to_remove.append(element)
                elem_id = element.get('id', '')
                elem_class = element.get('class', '')
                logger.info(f"  标记删除不可见元素(display:none): {element.tag} id='{elem_id[:30]}' class='{elem_class[:30]}'")

    # 处理 ng-hide 元素
    for element in ng_hide_elements:
        if element not in elements_to_remove:  # 避免重复添加
            elements_to_remove.append(element)
            elem_id = element.get('id', '')
            elem_class = element.get('class', '')
            logger.info(f"  标记删除不可见元素(ng-hide): {element.tag} id='{elem_id[:30]}' class='{elem_class[:30]}'")

    # 删除标记的元素及其所有子元素
    for element in elements_to_remove:
        try:
            parent = element.getparent()
            if parent is not None:
                # 记录删除前的信息
                elem_id = element.get('id', '')
                elem_class = element.get('class', '')
                child_count = len(element.xpath(".//*"))
                
                # 删除元素（会自动删除所有子元素）
                parent.remove(element)
                removed_count += 1
                
                logger.info(f"  ✓ 已删除: {element.tag} id='{elem_id[:30]}' class='{elem_class[:30]}' (包含{child_count}个子元素)")
        except Exception as e:
            logger.error(f"删除不可见元素时出错: {e}")
    
    logger.info(f"删除完成，共删除 {removed_count} 个不可见元素")
    
    return removed_count

def remove_page_level_header_footer(body):
    """
    激进删除页面级的header和footer - 基于多重特征判断
    """
    logger.info("执行激进删除页面级header和footer...")
    
    removed_count = 0
    
    # 第一轮：删除明确的语义标签
    semantic_tags = ["//header", "//footer", "//nav"]
    for tag_xpath in semantic_tags:
        elements = body.xpath(tag_xpath)
        for element in elements:
            try:
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
                    removed_count += 1
                    logger.info(f"  删除语义标签: {element.tag}")
            except Exception as e:
                logger.info(f"删除语义标签时出错: {e}")
    
    # 第二轮：删除具有强header/footer特征的顶级div容器
    top_divs = body.xpath("./div")  # 只检查body的直接子div
    
    containers_to_remove = []
    
    for div in top_divs:
        classes = div.get('class', '').lower()
        elem_id = div.get('id', '').lower()
        text_content = div.text_content().lower()
        
        is_header_footer = False
        
        # 强header特征
        strong_header_indicators = [
            'header', 'top', 'navbar', 'navigation', 'menu-main', 
            'site-header', 'page-header', 'banner', 'topbar'
        ]
        
        # 强footer特征
        strong_footer_indicators = [
            'footer', 'bottom', 'site-footer', 'page-footer', 
            'footerpc', 'wapfooter', 'g-bottom'
        ]
        
        # 检查类名和ID中的强特征
        for indicator in strong_header_indicators + strong_footer_indicators:
            if indicator in classes or indicator in elem_id:
                is_header_footer = True
                logger.info(f"  发现强结构特征: {indicator} in class/id")
                break
        
        # 基于内容的强特征判断（更严格的条件）
        if not is_header_footer:
            # Header内容特征（需要多个条件同时满足）
            header_words = [
                '登录', '注册', '首页', '主页', '无障碍', '办事', 
                '走进', '移动版', '手机版', '导航', '菜单', '搜索', '市政府',
                'login', 'register', 'home', 'menu', 'search', 'nav'
            ]
            header_count = sum(1 for word in header_words if word in text_content)
            
            # Footer内容特征（需要多个条件同时满足）
            footer_words =  [
                '网站说明', '网站标识码', '版权所有', '主办单位', '承办单位', 
                '技术支持', '联系我们', '网站地图', '隐私政策', '免责声明',
                '备案号', 'icp', '公安备案', '政府网站', '网站管理',
                'copyright', 'all rights reserved', 'powered by', 'designed by'
            ]
            footer_count = sum(1 for word in footer_words if word in text_content)
            
            text_length = len(text_content.strip())
            
            # 只有当特征词汇非常集中且容器相对较小时才删除
            if header_count >= 4 and text_length < 1000:
                is_header_footer = True
                logger.info(f"  发现强header内容特征: {header_count}个关键词")
            elif footer_count >= 3 and text_length < 800:
                is_header_footer = True
                logger.info(f"  发现强footer内容特征: {footer_count}个关键词")
        
        if is_header_footer:
            containers_to_remove.append(div)
    
    # 删除标记的容器
    for container in containers_to_remove:
        try:
            parent = container.getparent()
            if parent is not None:
                parent.remove(container)
                removed_count += 1
                logger.info(f"  删除页面级容器: {container.tag} class='{container.get('class', '')[:30]}'")
        except Exception as e:
            logger.error(f"删除页面级容器时出错: {e}")
    
    return removed_count

def calculate_text_density(element):
    """
    计算元素的文本密度 - 借鉴trafilatura的密度计算
    密度 = 文本长度 / (标签数量 + 链接数量 * 权重)
    """
    text_content = element.text_content().strip()
    text_length = len(text_content)
    
    if text_length == 0:
        return 0
    
    # 计算标签数量
    all_tags = element.xpath(".//*")
    tag_count = len(all_tags)
    
    # 计算链接数量（链接通常在导航中密集出现）
    links = element.xpath(".//a")
    link_count = len(links)
    
    # 计算图片数量
    images = element.xpath(".//img")
    image_count = len(images)
    
    # 密度计算：文本越多、标签越少、链接越少 = 密度越高
    # 链接密集的区域（如导航）会有较低密度
    denominator = max(1, tag_count + link_count * 2 + image_count * 0.5)
    density = text_length / denominator
    
    return density

def remove_low_density_containers(body):
    """
    第一步：移除低密度容器 - 主要针对导航、菜单等链接密集区域
    但要保护包含实际内容的容器
    """
    logger.info("执行第一步：移除低密度容器...")
    
    # 获取所有顶级容器（body的直接子元素）
    top_level_containers = body.xpath("./div | ./section | ./main | ./article | ./header | ./footer | ./nav | ./aside")
    
    containers_to_remove = []
    
    for container in top_level_containers:
        density = calculate_text_density(container)
        text_length = len(container.text_content().strip())
        links = container.xpath(".//a")
        
        # 检查是否包含重要内容标识符 - 保护这些容器
        classes = container.get('class', '').lower()
        elem_id = container.get('id', '').lower()
        
        # 重要内容标识符 - 这些容器通常包含主要内容
        important_indicators = [
            'content', 'main', 'article', 'detail', 'news', 'info',
            'bg-fff', 'bg-white', 'wrapper', 'body'  # 添加常见的内容容器类名
        ]
        
        has_important_content = any(indicator in classes or indicator in elem_id 
                                  for indicator in important_indicators)
        
        # 检查是否包含文章特征（时间、标题等）
        has_article_features = bool(
            container.xpath(".//h1 | .//h2 | .//h3") or  # 标题
            container.xpath(".//*[contains(text(), '发布时间') or contains(text(), '来源') or contains(text(), '浏览次数')]") or  # 文章元信息
            len(container.xpath(".//p")) > 3  # 多个段落
        )
        
        # 如果包含重要内容或文章特征，跳过删除
        if has_important_content or has_article_features:
            logger.info(f"  保护重要内容容器: class='{classes[:30]}' (包含重要内容标识或文章特征)")
            continue
        
        # 低密度且链接密集的容器很可能是导航
        link_ratio = len(links) / max(1, len(container.xpath(".//*")))
        
        # 判断是否为低质量容器
        is_low_quality = False
        
        # 条件1：密度极低且链接比例高（典型导航特征）
        if density < 5 and link_ratio > 0.3:
            is_low_quality = True
            logger.info(f"  发现低密度高链接容器: 密度={density:.2f}, 链接比例={link_ratio:.2f}")
        
        # 条件2：文本很少但标签很多（可能是复杂的导航结构）
        elif text_length < 200 and len(container.xpath(".//*")) > 20:
            is_low_quality = True
            logger.info(f"  发现少文本多标签容器: 文本长度={text_length}, 标签数={len(container.xpath('.//*'))}")
        
        # 条件3：链接文本占总文本比例过高（但文本长度要足够少，避免误删内容页）
        elif links and text_length < 500:  # 增加文本长度限制
            link_text_length = sum(len(link.text_content()) for link in links)
            if text_length > 0 and link_text_length / text_length > 0.8:  # 提高阈值
                is_low_quality = True
                logger.info(f"  发现链接文本占比过高容器: 链接文本比例={link_text_length/text_length:.2f}")
        
        if is_low_quality:
            containers_to_remove.append(container)
    
    # 删除低质量容器
    removed_count = 0
    for container in containers_to_remove:
        try:
            parent = container.getparent()
            if parent is not None:
                parent.remove(container)
                removed_count += 1
        except Exception as e:
            logger.error(f"删除低密度容器时出错: {e}")
    
    logger.info(f"第一步完成：移除了 {removed_count} 个低密度容器")
    return body

def remove_semantic_interference_tags(body):
    """
    第二步：强制移除语义干扰标签 - trafilatura的结构特征识别
    """
    logger.info("执行第二步：移除语义干扰标签...")
    
    # 强制移除的语义标签
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
                    logger.info(f"  移除语义标签: {element.tag} {element.get('class', '')[:30]}")
            except Exception as e:
                logger.info(f"删除语义标签时出错: {e}")
    
    logger.info(f"第二步完成：移除了 {removed_count} 个语义干扰标签")
    return body

def remove_positional_interference(body):
    """
    第四步：基于位置的最终清理 - 移除页面顶部和底部的干扰容器
    """
    logger.info("执行第四步：移除位置干扰容器...")
    
    # 获取body的所有直接子容器
    direct_children = body.xpath("./div | ./section | ./main | ./article")
    
    if len(direct_children) <= 2:
        logger.info("容器数量太少，跳过位置清理")
        return body
    
    containers_to_remove = []
    
    # 分析第一个和最后一个容器
    first_container = direct_children[0] if direct_children else None
    last_container = direct_children[-1] if len(direct_children) > 1 else None
    
    # 检查第一个容器是否为头部干扰
    if first_container is not None:
        if is_positional_header(first_container):
            containers_to_remove.append(first_container)
            logger.info(f"  标记移除头部容器: {first_container.tag}")
    
    # 检查最后一个容器是否为尾部干扰
    if last_container is not None and last_container != first_container:
        if is_positional_footer(last_container):
            containers_to_remove.append(last_container)
            logger.info(f"  标记移除尾部容器: {last_container.tag}")
    
    # 删除位置干扰容器
    removed_count = 0
    for container in containers_to_remove:
        try:
            parent = container.getparent()
            if parent is not None:
                parent.remove(container)
                removed_count += 1
        except Exception as e:
            logger.error(f"删除位置容器时出错: {e}")
    
    logger.info(f"第四步完成：移除了 {removed_count} 个位置干扰容器")
    return body

def is_positional_header(container):
    """判断容器是否为位置上的头部干扰"""
    text_content = container.text_content().lower()
    
    # 头部特征词汇
    header_indicators = [
        '登录', '注册', '首页', '主页', '导航', '菜单', '搜索',
        '政务服务', '办事服务', '互动交流', '走进', '无障碍',
        'login', 'register', 'home', 'menu', 'search', 'nav'
    ]
    
    # 计算头部特征词汇出现次数
    header_count = sum(1 for word in header_indicators if word in text_content)
    
    # 计算文本密度
    density = calculate_text_density(container)
    
    # 判断条件：包含多个头部词汇 或 密度很低且包含头部词汇
    return header_count >= 3 or (density < 8 and header_count >= 2)

def is_positional_footer(container):
    """判断容器是否为位置上的尾部干扰"""
    text_content = container.text_content().lower()
    
    # 尾部特征词汇
    footer_indicators = [
        '版权所有', '主办单位', '承办单位', '技术支持', '联系我们',
        '网站地图', '隐私政策', '免责声明', '备案号', 'icp',
        '网站标识码', '政府网站', '网站管理',
        'copyright', 'all rights reserved', 'powered by'
    ]
    
    # 计算尾部特征词汇出现次数
    footer_count = sum(1 for word in footer_indicators if word in text_content)
    
    # 计算文本密度
    density = calculate_text_density(container)
    
    # 判断条件：包含多个尾部词汇 或 密度很低且包含尾部词汇
    return footer_count >= 2 or (density < 6 and footer_count >= 1)

def is_interference_container(container):
    """
    判断是否为需要删除的干扰容器 - 融合trafilatura的多维度判断
    """
    classes = container.get('class', '').lower()
    elem_id = container.get('id', '').lower()
    tag_name = container.tag.lower()
    text_content = container.text_content().lower()
    
    # 1. 强制删除的语义标签 - trafilatura的结构特征
    if tag_name in ['header', 'footer', 'nav', 'aside']:
        return True
    
    # 2. 强制删除的结构特征关键词
    strong_interference_keywords = [
        'header', 'footer', 'nav', 'navigation', 'menu', 'menubar', 
        'topbar', 'bottom', 'sidebar', 'aside', 'banner', 'breadcrumb'
    ]
    
    for keyword in strong_interference_keywords:
        if keyword in classes or keyword in elem_id:
            return True
    
    # 3. 基于内容密度的判断 - trafilatura的密度分析
    density = calculate_text_density(container)
    text_length = len(text_content.strip())
    
    # 低密度 + 短文本 = 很可能是导航或装饰性元素
    if density < 3 and text_length < 300:
        return True
    
    # 4. 基于链接密度的判断 - trafilatura会分析链接分布
    links = container.xpath(".//a")
    if len(links) > 5:
        link_text_length = sum(len(link.text_content()) for link in links)
        if text_length > 0:
            link_ratio = link_text_length / text_length
            # 链接文本占比过高，很可能是导航
            if link_ratio > 0.7:
                return True
    
    # 5. 基于内容特征的精确判断
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
    
    # 计算内容特征匹配度
    header_matches = sum(1 for pattern in header_content_patterns if pattern in text_content)
    footer_matches = sum(1 for pattern in footer_content_patterns if pattern in text_content)
    
    # 降低阈值，更严格地识别干扰内容
    if header_matches >= 2:  # 从3降到2
        return True
    
    if footer_matches >= 2:  # 从3降到2
        return True
    
    # 6. 基于位置和大小的综合判断
    # 很小的容器但包含多个特征词汇，很可能是干扰
    if text_length < 200 and (header_matches + footer_matches) >= 2:
        return True
    
    # 7. 特殊情况：广告和社交媒体相关
    ad_keywords = ['advertisement', 'ads', 'social', 'share', 'follow', 'subscribe']
    ad_matches = sum(1 for keyword in ad_keywords if keyword in text_content or keyword in classes)
    if ad_matches >= 2:
        return True
    
    return False

def find_article_container(page_tree):
    """
    查找文章容器
    返回: (main_content, cleaned_body) - 主内容容器和清理后的body
    """
    cleaned_body = preprocess_html_remove_interference(page_tree)
    
    if cleaned_body is None:
        logger.error("清理后的body为None")
        return None, None
    
    main_content = find_main_content_in_cleaned_html(cleaned_body)
    
    # 返回主内容容器和清理后的body（确保使用清理后的tree）
    return main_content, cleaned_body

def extract_content_to_markdown(html_content: str):
    """
    从HTML内容中提取正文并转换为Markdown格式
    
    Args:
        html_content: 输入的HTML内容字符串
        
    Returns:
        dict: 包含markdown内容、xpath和状态的字典
    """
    try:
        # 解析HTML
        tree = html.fromstring(html_content)
        
        # 获取主内容容器（从清理后的tree中获取）
        main_container, cleaned_body = find_article_container(tree)
        
        if main_container is None or cleaned_body is None:
            logger.error("未找到主内容容器")
            return {
                'markdown_content': '',
                'html_content': '',  # 未找到容器时返回空HTML
                'xpath': '',
                'status': 'failed'
            }
        
        logger.info("✓ 使用清理后的HTML tree进行内容提取")
        
        # 生成XPath
        xpath = generate_xpath(main_container)
        
        # 获取容器的HTML内容
        container_html = html.tostring(main_container, encoding='unicode', pretty_print=True)
        cleaned_container_html = clean_container_html(container_html)
        # 转换为Markdown
        markdown_content = markdownify.markdownify(
            cleaned_container_html,
            heading_style="ATX",  # 使用 # 格式的标题
            bullets="-",  # 使用 - 作为列表符号
            strip=['script', 'style']  # 第二次移除script和style标签（这里的清除效果貌似不是很好，script标签没有正确的去除）
        )
        
        # 清理Markdown内容
        markdown_content = clean_markdown_content(markdown_content)
        
        logger.info(f"成功提取内容，XPath: {xpath}")
        logger.info(f"Markdown内容长度: {len(markdown_content)}")
        logger.info(f"HTML内容长度: {len(cleaned_container_html)}")
        
        return {
            'markdown_content': markdown_content,
            'html_content': cleaned_container_html,  # 返回清理后的HTML内容
            'xpath': xpath,
            'status': 'success'
        }
        
    except Exception as e:
        import traceback
        error_msg = str(e) if str(e) else repr(e)
        logger.error(f"提取内容时出错: {error_msg}")
        logger.error(f"错误类型: {type(e).__name__}")
        logger.error(f"完整堆栈:\n{traceback.format_exc()}")
        return {
            'markdown_content': '',
            'html_content': '',  # 错误时返回空HTML
            'xpath': '',
            'status': 'failed'
        }

def clean_container_html(container_html: str) -> str:
    """
    清理html内容，删除script、style和js代码
    """
    from bs4 import BeautifulSoup
    import re

    if not container_html or not isinstance(container_html, str):
        return container_html or ""

    try:
        # 解析HTML
        soup = BeautifulSoup(container_html, 'html.parser')
        
        # 删除script标签
        for script in soup.find_all('script'):
            if script:  # 确保不是None
                script.decompose()
        
        # 删除style标签
        for style in soup.find_all('style'):
            if style:  # 确保不是None
                style.decompose()

        # 1. 查找所有有style属性的元素
        styled_elements = soup.find_all(attrs={"style": True})
        
        display_none_elements = []
        for i, element in enumerate(styled_elements):
            style = element.get('style', '')
            if 'display' in style.lower() and 'none' in style.lower():
                display_none_elements.append(element)
                        
        # 尝试删除它们
        for element in display_none_elements:
            try:
                element.decompose()
            except Exception as e:
                pass
        result = str(soup)
        
        # 检查结果中是否还有display:none
        if 'display:none' in result.lower():
            # 找出残留的
            remaining = re.findall(r'<[^>]*display\s*:\s*none[^>]*>', result, re.IGNORECASE)

        # 安全地删除JavaScript相关属性
        all_tags = soup.find_all()
        for tag in all_tags:
            if tag is None or not hasattr(tag, 'attrs'):
                continue
                
            attrs_to_remove = []
            # 安全地遍历属性
            for attr_name in list(tag.attrs.keys()):  # 使用list避免在迭代中修改
                if attr_name.startswith('on'):  # onclick, onload等
                    attrs_to_remove.append(attr_name)
                elif (attr_name == 'href' and 
                      tag.get(attr_name) and 
                      str(tag[attr_name]).startswith('javascript:')):
                    attrs_to_remove.append(attr_name)
            
            # 安全删除属性
            for attr in attrs_to_remove:
                try:
                    del tag[attr]
                except (AttributeError, KeyError):
                    pass  # 属性可能已被删除
        
        # 返回清理后的HTML
        return str(soup)
        
    except Exception as e:
        # 如果发生错误，返回原始内容或空字符串
        print(f"清理HTML时出错: {e}")
        return container_html
def clean_markdown_content(markdown_content: str) -> str:
    """
    清理Markdown内容
    
    Args:
        markdown_content: 原始Markdown内容
        
    Returns:
        str: 清理后的Markdown内容
    """
    # 移除多余的空行
    markdown_content = re.sub(r'\n\s*\n\s*\n', '\n\n', markdown_content)
    
    # 移除行首行尾的空白字符
    lines = [line.strip() for line in markdown_content.split('\n')]
    
    # 过滤空行，但保留段落间的分隔
    cleaned_lines = []
    prev_empty = False
    
    for line in lines:
        if line.strip():
            cleaned_lines.append(line)
            prev_empty = False
        elif not prev_empty:
            cleaned_lines.append('')
            prev_empty = True
    
    # 移除开头和结尾的空行
    while cleaned_lines and not cleaned_lines[0]:
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
    
    return '\n'.join(cleaned_lines)

def find_main_content_in_cleaned_html(cleaned_body):
    """在清理后的HTML中查找主内容区域"""
    
    if cleaned_body is None:
        logger.error("cleaned_body为None，无法查找内容")
        return None
    
    # 获取所有可能的内容容器
    content_containers = cleaned_body.xpath(".//div | .//section | .//article | .//main")
    
    if not content_containers:
        logger.info("未找到内容容器，返回body")
        return cleaned_body
    
    # 对容器进行评分，同时删除大幅度减分的标签
    scored_containers = []
    containers_to_remove = []
    
    for container in content_containers:
        if container is None:
            logger.warning("跳过None容器")
            continue
            
        score = calculate_content_container_score(container)
        
        # 强保护：检查是否包含 printContent 或其他重要内容
        classes = container.get('class', '').lower()
        elem_id = container.get('id', '').lower()
        
        # 绝对保护的条件
        is_protected = (
            'printcontent' in elem_id.lower() or  # printContent ID
            container.xpath(".//*[@id='printContent' or @id='printcontent']") or  # 包含 printContent 子元素
            'bg-fff' in classes or  # 常见的内容容器类名
            'container' in classes and len(container.xpath(".//*")) > 20  # 大型容器且子元素多
        )
        
        if is_protected:
            scored_containers.append((container, max(score, 50)))  # 保护的容器至少给50分
            logger.info(f"保护重要容器: {container.tag} class='{classes[:30]}' 原分数: {score} -> 保护分数: {max(score, 50)}")
        elif score < -100:
            containers_to_remove.append(container)
            logger.info(f"标记删除大幅减分容器: {container.tag} class='{container.get('class', '')[:30]}' 得分: {score}")
        elif score > -50:  # 只考虑分数不太低的容器
            scored_containers.append((container, score))
    
    # 不删除任何容器，只是标记为不考虑
    logger.info(f"标记了 {len(containers_to_remove)} 个大幅减分的容器，但不删除以保护内容完整性")
    
    if not scored_containers:
        logger.info("未找到正分容器，返回第一个容器")
        return content_containers[0]
    
    # 选择得分最高的容器
    scored_containers.sort(key=lambda x: x[1], reverse=True)
    
    # 输出前5名容器的详细信息
    logger.info("\n" + "="*80)
    logger.info("📊 容器评分排行榜 (Top 5):")
    logger.info("="*80)
    
    top_5 = scored_containers[:5]
    for idx, (container, score) in enumerate(top_5, 1):
        classes = container.get('class', '')
        elem_id = container.get('id', '')
        text_length = len(container.text_content().strip())
        child_count = len(container.xpath(".//*"))
        
        logger.info(f"\n🏆 排名 #{idx} - 得分: {score}")
        logger.info(f"   标签: {container.tag}")
        logger.info(f"   类名: {classes[:80]}{'...' if len(classes) > 80 else ''}")
        logger.info(f"   ID: {elem_id[:50]}{'...' if len(elem_id) > 50 else ''}")
        logger.info(f"   文本长度: {text_length} 字符")
        logger.info(f"   子元素数: {child_count}")
    
    logger.info("\n" + "="*80)
    
    # 智能选择容器：优先选择更精确的容器的父容器
    best_score = scored_containers[0][1]
    
    # ---------------------------------------------------------------------------------------------原方法，对于极为复杂的页面会定位的“过于准确”
    # same_score_containers = [container for container, score in scored_containers if score == best_score]
    # if len(same_score_containers) > 1:
    #     # 检查层级关系，层级关系。这一步直接影响结果的范围，对于某些范围大的页面，你可以考虑不获取最佳的，而获取次佳的容器 
    #     best_container = select_best_from_same_score_containers(same_score_containers)
    # else:
    #     best_container = scored_containers[0][0]
    # logger.info(f"选择最佳内容容器，得分: {best_score}")
    # logger.info(f"容器信息: {best_container.tag} class='{best_container.get('class', '')[:50]}'")
    # ---------------------------------------------------------------------------------------------
    # 智能容器选择策略
    logger.info("\n🤔 开始智能容器选择...")
    
    # 检查前5名容器是否都有长内容
    top_5_containers = scored_containers[:5]
    long_content_containers = []
    
    for container, score in top_5_containers:
        text_length = len(container.text_content().strip())
        classes = container.get('class', '')
        elem_id = container.get('id', '')
        
        if text_length > 1000:  # 长内容阈值
            long_content_containers.append((container, score, text_length))
            logger.info(f"   ✓ 发现长内容容器: 得分={score}, 长度={text_length}")
            logger.info(f"      标签={container.tag}, class='{classes}', id='{elem_id}'")
    
    # 策略1：如果有多个长内容容器且分数相近，选择更小（更精确）的
    if len(long_content_containers) >= 2:
        # 检查分数差距
        scores = [score for _, score, _ in long_content_containers]
        max_score = max(scores)
        min_score = min(scores)
        score_diff = max_score - min_score
        
        logger.info(f"   发现 {len(long_content_containers)} 个长内容容器")
        logger.info(f"   分数范围: {min_score} ~ {max_score}, 差距: {score_diff}")
        
        if score_diff <= 200:  # 分数差距不大
            logger.info("   ✓ 分数差距较小，优先选择更精确的容器")
            
            # 按子元素数量排序（子元素少的更精确）
            long_content_containers.sort(key=lambda x: len(x[0].xpath(".//*")))
            
            # 选择子元素最少但内容足够长的容器
            selected_precise_container = None
            selected_text_length = 0  # 记录选中容器的文本长度
            for container, score, text_length in long_content_containers:
                child_count = len(container.xpath(".//*"))
                classes = container.get('class', '')
                elem_id = container.get('id', '')
                
                logger.info(f"   候选: 得分={score}, 长度={text_length}, 子元素={child_count}")
                logger.info(f"      标签={container.tag}, class='{classes}', id='{elem_id}'")
                
                # 确保不是过度精确（子元素太少可能丢失内容）
                if child_count >= 10 or text_length > 3000:
                    selected_precise_container = container
                    selected_text_length = text_length  # 保存文本长度
                    logger.info(f"   ✅ 找到精确容器")
                    break
            
            if selected_precise_container is not None:
                # 向上遍历找到有意义的父容器
                def find_meaningful_parent(element):
                    """
                    向上遍历找到有意义的父容器：
                    1. 必须是 div、table、section、article、main 等容器标签
                    2. 必须有 class 或 id 属性
                    3. 不能是 body 标签
                    """
                    current = element.getparent()
                    depth = 0
                    max_depth = 5  # 最多向上查找5层
                    
                    # 有意义的容器标签
                    meaningful_tags = ['div','section', 'article', 'main']
                    
                    while current is not None and depth < max_depth:
                        tag = current.tag.lower()
                        classes = current.get('class', '').strip()
                        elem_id = current.get('id', '').strip()
                        
                        logger.info(f"   🔍 检查第{depth+1}层父节点: {tag}, class='{classes[:30]}', id='{elem_id[:30]}'")
                        
                        # 到达body就停止
                        if tag == 'body':
                            logger.info(f"      ⛔ 到达body标签，停止向上查找")
                            break
                        
                        # 检查是否是有意义的容器
                        is_meaningful_tag = tag in meaningful_tags
                        has_identifier = bool(classes or elem_id)
                        
                        if is_meaningful_tag and has_identifier:
                            logger.info(f"      ✅ 找到有意义的父容器: {tag}")
                            return current, depth + 1
                        elif not is_meaningful_tag:
                            logger.info(f"      ⏭ 跳过无意义标签: {tag}")
                        elif not has_identifier:
                            logger.info(f"      ⏭ 跳过无标识符的容器")
                        
                        current = current.getparent()
                        depth += 1
                    
                    logger.info(f"   ⚠ 未找到有意义的父容器（已查找{depth}层）")
                    return None, 0
                
                parent_container, parent_depth = find_meaningful_parent(selected_precise_container)
                
                if parent_container is not None:
                    # 检查父容器是否合理
                    parent_classes = parent_container.get('class', '')
                    parent_id = parent_container.get('id', '')
                    parent_text_length = len(parent_container.text_content().strip())
                    parent_child_count = len(parent_container.xpath(".//*"))
                    
                    logger.info(f"   📦 找到的父容器（向上{parent_depth}层）:")
                    logger.info(f"      标签={parent_container.tag}, class='{parent_classes}', id='{parent_id}'")
                    logger.info(f"      文本长度={parent_text_length}, 子元素={parent_child_count}")
                    
                    # 检查父容器是否包含干扰特征
                    parent_combined = f"{parent_classes} {parent_id}".lower()
                    has_interference = any(keyword in parent_combined for keyword in 
                                         ['header', 'footer', 'nav', 'menu', 'sidebar'])
                    
                    if not has_interference and parent_text_length > selected_text_length * 0.8:
                        best_container = parent_container
                        logger.info(f"   ✅ 选择父容器 (避免过度精确)")
                    else:
                        best_container = selected_precise_container
                        if has_interference:
                            logger.info(f"   ⚠ 父容器包含干扰特征，保持精确容器")
                        else:
                            logger.info(f"   ⚠ 父容器内容差异过大，保持精确容器")
                else:
                    best_container = selected_precise_container
                    logger.info(f"   ⚠ 无有效父容器，保持精确容器")
            else:
                # 如果都太小，选择得分最高的
                best_container = scored_containers[0][0]
                logger.info(f"   ⚠ 所有候选容器都太小，选择得分最高的")
        else:
            # 分数差距大，直接选择得分最高的
            best_container = scored_containers[0][0]
            logger.info(f"   分数差距较大，选择得分最高的容器")
    else:
        # 策略2：使用原有的父子关系选择逻辑
        logger.info("   使用父子关系选择策略...")
        
        # 设置分数阈值，考虑分数相近的容器
        score_threshold = 20
        similar_score_containers = [(container, score) for container, score in scored_containers 
                                   if abs(score - best_score) <= score_threshold]
        
        logger.info(f"   找到 {len(similar_score_containers)} 个分数相近的容器")
        
        if len(similar_score_containers) > 1:
            best_container = select_best_container_prefer_child(
                [c for c, s in similar_score_containers], 
                scored_containers
            )
        else:
            best_container = scored_containers[0][0]
    
    # 最终安全检查：确保选中的容器不包含干扰特征
    def has_interference_keywords(container):
        """检查容器的class/id是否包含干扰关键词"""
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
        logger.info(f"   ⚠️ 警告：选中的容器包含干扰关键词 '{keyword}'")
        logger.info(f"   🔄 尝试从候选列表中选择下一个容器...")
        
        # 从scored_containers中找到下一个不包含干扰特征的容器
        for container, score in scored_containers:
            has_interference_check, _ = has_interference_keywords(container)
            if not has_interference_check and score > 0:
                logger.info(f"   ✅ 找到替代容器，得分: {score}")
                logger.info(f"      标签={container.tag}, class='{container.get('class', '')}', id='{container.get('id', '')}'")
                best_container = container
                break
        else:
            logger.info(f"   ⚠️ 未找到合适的替代容器，保持原选择（但可能不准确）")
    
    # 获取最终选择的容器分数（如果是父容器，可能不在原始列表中）
    try:
        final_score = next(score for container, score in scored_containers if container == best_container)
    except StopIteration:
        # 如果best_container不在scored_containers中（比如选择了父容器），重新计算分数
        logger.info("   ℹ 最终容器不在原始评分列表中，重新计算分数...")
        final_score = calculate_content_container_score(best_container)
        logger.info(f"   重新计算的得分: {final_score}")
    
    final_text_length = len(best_container.text_content().strip())
    final_child_count = len(best_container.xpath(".//*"))
    
    logger.info("\n" + "="*80)
    logger.info("🎯 最终选择结果:")
    logger.info(f"   得分: {final_score}")
    logger.info(f"   标签: {best_container.tag}")
    logger.info(f"   类名: {best_container.get('class', '')[:80]}")
    logger.info(f"   ID: {best_container.get('id', '')[:50]}")
    logger.info(f"   文本长度: {final_text_length} 字符")
    logger.info(f"   子元素数: {final_child_count}")
    logger.info("="*80 + "\n")
    
    return best_container
def is_child_of(child_element, parent_element):
    """检查child_element是否是parent_element的子节点"""
    current = child_element.getparent()
    while current is not None:
        if current == parent_element:
            return True
        current = current.getparent()
    return False

def select_best_container_prefer_child(similar_containers, all_scored_containers):
    """从分数相近的容器中选择最佳的，优先选择子节点"""
    
    # 检查容器之间的父子关系
    parent_child_pairs = []
    
    for i, container1 in enumerate(similar_containers):
        for j, container2 in enumerate(similar_containers):
            if i != j:
                # 检查container2是否是container1的子节点
                if is_child_of(container2, container1):
                    # 获取两个容器的分数
                    score1 = next(score for c, score in all_scored_containers if c == container1)
                    score2 = next(score for c, score in all_scored_containers if c == container2)
                    parent_child_pairs.append((container1, container2, score1, score2))
                    logger.info(f"发现父子关系: 父容器得分{score1}, 子容器得分{score2}")
    
    # 如果找到父子关系，需要更严格的判断
    if parent_child_pairs:
        # 找出所有符合条件的子节点（分数差距小于20分，更严格）
        valid_children = []
        for parent, child, parent_score, child_score in parent_child_pairs:
            score_diff = parent_score - child_score
            # 只有当子节点分数差距很小时才考虑选择子节点
            if score_diff <= 20 and child_score >= 150:  # 子节点本身分数要足够高
                valid_children.append((child, child_score, score_diff))
        
        if valid_children:
            # 按分数排序，选择分数最高的子节点
            valid_children.sort(key=lambda x: (-x[1], x[2]))  # 按子节点分数降序，分差升序
            
            best_child, best_score, score_diff = valid_children[0]
            
            # 额外检查：确保选择的子节点确实比父节点更精确
            # 检查子节点的内容密度是否更高
            child_text_length = len(best_child.text_content().strip())
            parent_candidates = [parent for parent, child, p_score, c_score in parent_child_pairs 
                               if child == best_child]
            
            if parent_candidates:
                parent = parent_candidates[0]
                parent_text_length = len(parent.text_content().strip())
                
                # 如果子节点的内容长度不到父节点的60%，可能选择了错误的子节点
                if child_text_length < parent_text_length * 0.6:
                    logger.info(f"子节点内容过少({child_text_length} vs {parent_text_length})，选择父节点")
                    return parent
            
            logger.info(f"选择子容器: {best_child.tag} class='{best_child.get('class', '')}' (父子分差: {score_diff})")
            return best_child
    
    # 如果没有合适的父子关系，使用原来的层级深度选择逻辑
    return select_deepest_container_from_similar(similar_containers)
def select_deepest_container_from_similar(similar_containers):
    """从分数相近的容器中选择层级最深的一个"""
    if not similar_containers:
        return None
    
    if len(similar_containers) == 1:
        return similar_containers[0]
    
    # 计算每个容器的层级深度
    container_depths = []
    for container in similar_containers:
        depth = calculate_container_depth(container)
        container_depths.append((container, depth))
        logger.info(f"  候选容器层级深度: {depth} - {container.tag} class='{container.get('class', '')}'")
    
    # 按层级深度排序（深度越大，层级越深）
    container_depths.sort(key=lambda x: x[1], reverse=True)
    
    # 选择层级最深的容器
    deepest_container = container_depths[0][0]
    deepest_depth = container_depths[0][1]
    
    logger.info(f"选择最深层容器 (深度 {deepest_depth}): {deepest_container.tag} class='{deepest_container.get('class', '')}'")
    return deepest_container

def calculate_container_depth(container):
    """计算容器距离body的层级深度"""
    depth = 0
    current = container
    
    # 向上遍历直到body或html
    while current is not None and current.tag not in ['body', 'html']:
        depth += 1
        current = current.getparent()
        if current is None:
            break
    
    return depth
def select_best_from_same_score_containers(containers):
    """从得分相同的多个容器中选择层级最深的一个（儿子容器）"""
    # 检查容器之间的层级关系，选择层级最深的
    container_depths = []
    
    for container in containers:
        # 计算容器的层级深度（距离body的层级数）
        depth = calculate_container_depth(container)
        container_depths.append((container, depth))
        
        logger.info(f"容器层级深度: {depth} - {container.tag} class='{container.get('class', '')[:30]}'")
    
    # 按层级深度排序（深度越大，层级越深）
    container_depths.sort(key=lambda x: x[1], reverse=True)
    
    # 选择层级最深的容器（儿子容器）
    best_container = container_depths[0][0]
    best_depth = container_depths[0][1]
    
    logger.info(f"选择层级最深的容器 (深度 {best_depth}): {best_container.tag} class='{best_container.get('class', '')[:30]}'")
    
    return best_container

def calculate_container_depth(container):
    """计算容器距离body的层级深度"""
    depth = 0
    current = container
    
    # 向上遍历直到body或html
    while current is not None and current.tag not in ['body', 'html']:
        depth += 1
        current = current.getparent()
        if current is None:
            break
    
    return depth
def calculate_content_container_score(container):
    """计算内容容器得分 - 专注于识别真正的内容区域，大幅度减分干扰标签"""
    if container is None:
        logger.error("容器为None，无法计算得分")
        return -1000
    
    score = 0
    debug_info = []
    
    classes = container.get('class', '').lower()
    elem_id = container.get('id', '').lower()
    text_content = container.text_content()
    text_length = len(text_content.strip())

    logger.info(f"\n=== 开始评分容器 ===")
    logger.info(f"标签: {container.tag}")
    logger.info(f"类名: {classes[:100]}{'...' if len(classes) > 100 else ''}")
    logger.info(f"ID: {elem_id[:50]}{'...' if len(elem_id) > 50 else ''}")
    logger.info(f"文本长度: {text_length}")

    # 0. 检查 display:none - 直接排除不可见元素
    style = container.get('style', '').lower()
    if 'display' in style and 'none' in style:
        score -= 1000  # 极大减分，基本排除
        debug_info.append("❌ display:none 不可见元素: -1000")
        logger.warning(f"⚠️ 警告：在评分阶段发现 display:none 元素（应该已被删除）")
        logger.warning(f"   元素: {container.tag} id='{elem_id[:30]}' class='{classes[:30]}'")
        logger.info("❌ 发现 display:none，这是不可见元素，直接排除")
        logger.info(f"最终得分: {score}")
        return score
    
    # 检查祖先元素是否有 display:none
    current = container.getparent()
    depth = 0
    while current is not None and depth < 3:  # 检查3层祖先
        parent_style = current.get('style', '').lower()
        if 'display' in parent_style and 'none' in parent_style:
            score -= 800  # 祖先不可见，也要大幅减分
            debug_info.append(f"❌ 祖先元素 display:none (第{depth+1}层): -800")
            logger.info(f"❌ 第{depth+1}层祖先元素有 display:none，大幅减分")
            logger.info(f"最终得分: {score}")
            return score
        current = current.getparent()
        depth += 1

    # # 特殊ID加分 - printContent通常是主要内容区域
    # special_id_keywords = ['printcontent', 'printContent']
    # for keyword in special_id_keywords:
    #     if keyword.lower() in elem_id.lower():
    #         if 'printcontent' in keyword.lower():
    #             score += 200  # printContent给最高分
    #             debug_info.append("✓ printContent ID特征: +200")
    #         else:
    #             score += 100  # 其他内容ID也给高分
    #             debug_info.append(f"✓ 内容ID特征: +100 ({keyword})")
    #         break
    # 首先进行大幅度减分检查 - 直接排除干扰标签
    # 1. 检查标签名 - 直接排除
    if container.tag.lower() in ['header', 'footer', 'nav', 'aside']:
        score -= 500  # 极大减分，基本排除
        debug_info.append(f"❌ 干扰标签: -{500} ({container.tag}) - 直接排除")
        logger.info(f"❌ 发现干扰标签 {container.tag}，直接排除，得分: {score}")
        return score  # 直接返回，不再计算其他分数
    
    # -------------------------------------------------------------------------
    # 2. 检查强烈的干扰类名/ID - 大幅减分
    # strong_interference_keywords = [
    #     'header', 'footer', 'nav', 'navigation', 'menu', 'menubar', 
    #     'topbar', 'bottom', 'sidebar', 'aside', 'banner', 'ad', 'advertisement'
    # ]
    
    # interference_count = 0
    # found_interference_keywords = []
    # for keyword in strong_interference_keywords:
    #     if keyword in classes or keyword in elem_id:
    #         interference_count += 1
    #         found_interference_keywords.append(keyword)
    
    # if interference_count > 0:
    #     interference_penalty = interference_count * 200  # 每个干扰关键词减200分
    #     score -= interference_penalty
    #     debug_info.append(f"❌ 强干扰特征: -{interference_penalty} (发现{interference_count}个: {', '.join(found_interference_keywords)})")
    #     logger.info(f"❌ 发现强干扰特征: {', '.join(found_interference_keywords)}，减分: {interference_penalty}")
        
    #     # 如果干扰特征太多，直接返回负分
    #     if interference_count >= 2:
    #         logger.info(f"❌ 干扰特征过多({interference_count}个)，直接返回负分: {score}")
    #         return score
    # ----------------------------------------------------------------------------

    # 2. 基于class/id的语义判断 - 这是最可靠的判断方式
    
    # 2.1 强干扰特征（导航、头部、尾部等）- 大幅减分
    strong_interference_keywords = [
        'header', 'footer', 'nav', 'navigation', 'menu', 'menubar', 
        'topbar', 'bottom', 'sidebar', 'aside', 'banner', 'ad', 'advertisement'
    ]

    def create_pattern(keyword):
        # 匹配单词边界，或被 -/_/space 包围
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
        debug_info.append(f"❌ 强干扰特征: -{interference_penalty} (发现{interference_count}个: {', '.join(found_interference_keywords)})")
        logger.info(f"❌ 发现强干扰特征: {', '.join(found_interference_keywords)}，减分: {interference_penalty}")
        
        if interference_count >= 2:
            logger.info(f"❌ 干扰特征过多({interference_count}个)，直接返回负分: {score}")
            return score
    
    # 2.2 正面内容特征 - 适当加分
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
        # 正面特征加分，但不要加太多
        positive_bonus = min(positive_count * 30, 90)
        score += positive_bonus
        debug_info.append(f"✓ 正面内容特征: +{positive_bonus} (发现{positive_count}个: {', '.join(found_positive_keywords)})")
        logger.info(f"✓ 发现正面内容特征: {', '.join(found_positive_keywords)}，加分: {positive_bonus}")

    # 4. 检查内容特征 - 识别首部尾部内容
    header_content_keywords = [
        '登录', '注册', '首页', '主页', '无障碍',  '办事',   '无障碍浏览','打印','收藏','机构概况','在线服务','互动交流',
        '走进', '移动版', '手机版', '导航', '菜单', '搜索', '市政府',
        'login', 'register', 'home', 'menu', 'search', 'nav'
    ]
    
    footer_content_keywords = [
        '网站说明', '网站标识码', '版权所有', '主办单位', '承办单位', 
        '技术支持', '联系我们', '网站地图', '隐私政策', '免责声明',
        '备案号', 'icp', '公安备案', '政府网站', '网站管理',
        'copyright', 'all rights reserved', 'powered by', 'designed by'
    ]
    
    # 详细记录找到的关键词
    found_header_keywords = [keyword for keyword in header_content_keywords if keyword in text_content.lower() and not (('当前位置' in text_content.lower()) or ('当前的位置' in  text_content.lower())) ]
    found_footer_keywords = [keyword for keyword in footer_content_keywords if keyword in text_content.lower()]
    
    header_content_count = len(found_header_keywords)
    footer_content_count = len(found_footer_keywords)
    
    # 3. 简化的链接密度检查（辅助判断）
    links = container.xpath(".//a[@href]")
    
    if links and text_length > 0:
        link_count = len(links)
        link_text_total = sum(len(link.text_content().strip()) for link in links)
        
        # 只计算最关键的指标：链接密度（每1000字符的链接数）
        links_per_100_chars = (link_count / text_length) * 10000
        link_text_ratio = link_text_total / text_length
        
        logger.info(f"🔗 链接分析: {link_count}个链接, 密度={links_per_100_chars:.2f}个/5000字符, 占比={link_text_ratio:.1%}")
        
        # 简单判断：链接密度过高就减分
        if link_count > 5 :
            if links_per_100_chars > 5:
                score -= 100
                debug_info.append(f"❌ 极高链接密度: -100")
            elif links_per_100_chars > 3:
                score -= 50
                debug_info.append(f"⚠ 高链接密度: -50")
    
    logger.info(f"📝 内容特征分析:")
    logger.info(f"   首部关键词({header_content_count}个): {found_header_keywords}")
    logger.info(f"   尾部关键词({footer_content_count}个): {found_footer_keywords}")
    
    # 判断是否为长文本内容（正文内容通常很长）
    is_long_content = text_length > 3000
    
    if is_long_content:
        logger.info(f"✓ 检测到长文本内容({text_length}字符)，降低首尾部关键词减分力度")
    
    if header_content_count >= 5:
        if is_long_content:
            score -= 100
            debug_info.append(f"⚠ 首部内容(长文本): -100 (发现{header_content_count}个关键词: {', '.join(found_header_keywords)})")
            logger.info(f"⚠ 首部内容过多\文本较长，减分100")
        else:
            score -= 300
            debug_info.append(f"❌ 首部内容: -300 (发现{header_content_count}个关键词: {', '.join(found_header_keywords)})")
            logger.info(f"❌ 首部内容过多，减分300")
    # 大幅减分首部尾部内容 - 但对长文本内容宽容处理
    elif header_content_count >= 3:
        if is_long_content:
            # 长文本内容，轻微减分
            score -= 1
            debug_info.append(f"⚠ 首部内容(长文本): -1 (发现{header_content_count}个关键词: {', '.join(found_header_keywords)})")
            logger.info(f"⚠ 首部内容过多但文本较长，轻微减分1")
        else:
            score -= 300
            debug_info.append(f"❌ 首部内容: -300 (发现{header_content_count}个关键词: {', '.join(found_header_keywords)})")
            logger.info(f"❌ 首部内容过多，减分300")
    elif header_content_count >= 2:
        if is_long_content:
            # 长文本内容，轻微减分
            score -= 1
            debug_info.append(f"⚠ 首部内容(长文本): -1 (发现{header_content_count}个关键词: {', '.join(found_header_keywords)})")
            logger.info(f"⚠ 首部内容较多但文本较长，轻微减分1")
        else:
            score -= 150
            debug_info.append(f"❌ 首部内容: -150 (发现{header_content_count}个关键词: {', '.join(found_header_keywords)})")
            logger.info(f"❌ 首部内容较多，减分150")
    
    if footer_content_count >= 3:
        if is_long_content:
            # 长文本内容，轻微减分
            score -= 100
            debug_info.append(f"⚠ 尾部内容(长文本): -100 (发现{footer_content_count}个关键词: {', '.join(found_footer_keywords)})")
            logger.info(f"⚠ 尾部内容过多但文本较长，轻微减分100")
        else:
            score -= 300
            debug_info.append(f"❌ 尾部内容: -300 (发现{footer_content_count}个关键词: {', '.join(found_footer_keywords)})")
            logger.info(f"❌ 尾部内容过多，减分300")
    elif footer_content_count >= 2:
        if is_long_content:
            # 长文本内容，轻微减分
            score -= 50
            debug_info.append(f"⚠ 尾部内容(长文本): -50 (发现{footer_content_count}个关键词: {', '.join(found_footer_keywords)})")
            logger.info(f"⚠ 尾部内容较多但文本较长，轻微减分50")
        else:
            score -= 150
            debug_info.append(f"❌ 尾部内容: -150 (发现{footer_content_count}个关键词: {', '.join(found_footer_keywords)})")
            logger.info(f"❌ 尾部内容较多，减分150")
    
    # 如果已经是严重负分，不再继续计算（但对长文本内容更宽容）
    if score < -200 and not is_long_content:
        logger.info(f"❌ 当前得分过低({score})，停止后续计算")
        debug_info.append(f"❌ 得分过低，停止计算: {score}")
        return score
    elif score < -200 and is_long_content:
        logger.info(f"⚠ 当前得分较低({score})，但文本较长({text_length}字符)，继续计算")
    
    # 5. 基础内容长度评分
    logger.info(f"📏 内容长度评分: {text_length}字符")
    if text_length > 5000:
        score+=200
        debug_info.append("✓ 超长内容: +200")
        logger.info(f"✓ 超长内容加分: +200")
    elif text_length > 1000:
        score += 50
        debug_info.append("✓ 长内容: +50")
        logger.info(f"✓ 长内容加分: +50")
    elif text_length > 500:
        score += 35
        debug_info.append("✓ 中等内容: +35")
        logger.info(f"✓ 中等内容加分: +35")
    elif text_length > 200:
        score += 20
        debug_info.append("✓ 短内容: +20")
        logger.info(f"✓ 短内容加分: +20")
    elif text_length < 50:
        score -= 20
        debug_info.append("❌ 内容太少: -20")
        logger.info(f"❌ 内容太少减分: -20")
    
    # 6. Role属性检查
    role = container.get('role', '').lower()
    logger.info(f"🎭 Role属性: '{role}'")
    if role == 'viewlist':
        score += 150
        debug_info.append("✓ Role特征: +150 (role='viewlist')")
        logger.info(f"✓ 发现viewlist角色，加分150")
    elif role in ['list', 'listbox', 'grid', 'main', 'article']:
        score += 50
        debug_info.append(f"✓ Role特征: +50 (role='{role}')")
        logger.info(f"✓ 发现{role}角色，加分50")
    
    # 7. 内容特征检测 - 不限于列表
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
    
    logger.info(f"🔍 内容特征检测:")
    for pattern, weight, feature_name in content_indicators:
        matches = re.findall(pattern, text_content)
        if matches:
            total_content_score += weight
            matched_features.append(f"{feature_name}({len(matches)})")
            logger.info(f"   ✓ {feature_name}: 找到{len(matches)}个匹配，加分{weight}")
    
    if total_content_score > 0:
        final_content_score = min(total_content_score, 120)
        score += final_content_score
        debug_info.append(f"✓ 内容特征: +{final_content_score} ({','.join(matched_features)})")
        logger.info(f"✓ 内容特征总加分: {final_content_score} (原始分数: {total_content_score})")
    else:
        logger.info(f"   ❌ 未发现内容特征")
    
    # 8. 额外的正面特征检查（已在步骤2.2中处理，避免重复加分）
    
    # 9. 结构化内容检测 - 不限于列表
    structured_elements = container.xpath(".//p | .//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6 | .//li | .//table | .//div[contains(@class,'content')] | .//section")
    if len(structured_elements) > 5:
        structure_score = min(len(structured_elements) * 2, 40)
        score += structure_score
        debug_info.append(f"结构化内容: +{structure_score}")
    
    # 10. 图片内容
    images = container.xpath(".//img")
    if len(images) > 0:
        image_score = min(len(images) * 3, 150)
        score += image_score
        debug_info.append(f"图片内容: +{image_score}")
    
    # 输出调试信息
    container_info = f"{container.tag} class='{classes[:30]}'"
    logger.info(f"容器评分: {score} - {container_info}")
    for info in debug_info:
        logger.info(f"  {info}")
    
    return score

def exclude_page_header_footer(body):
    """排除页面级别的header和footer"""
    children = body.xpath("./div | ./main | ./section | ./article")
    
    if not children:
        return body
    
    valid_children = []
    for child in children:
        if not is_page_level_header_footer(child):
            valid_children.append(child)
    
    return find_middle_content(valid_children)

def is_page_level_header_footer(element):
    """判断是否是页面级别的header或footer - 更严格的检查"""
    classes = element.get('class', '').lower()
    elem_id = element.get('id', '').lower()
    tag_name = element.tag.lower()
    
    # 检查标签名
    if tag_name in ['header', 'footer', 'nav']:
        return True
    
    # 检查是否在footer区域
    is_footer, _ = is_in_footer_area(element)
    if is_footer:
        return True
    
    # 检查页面级别的header/footer特征
    page_keywords = ['header', 'footer', 'nav', 'menu', 'topbar', 'bottom', 'top']
    for keyword in page_keywords:
        if keyword in classes or keyword in elem_id:
            return True
    
    # 检查role属性
    role = element.get('role', '').lower()
    if role in ['banner', 'navigation', 'contentinfo']:
        return True
    
    return False

def find_middle_content(valid_children):
    """从有效子元素中找到中间的主要内容"""
    if not valid_children:
        return None
    
    if len(valid_children) == 1:
        return valid_children[0]
    
    # 计算每个容器的内容得分
    scored_containers = []
    for container in valid_children:
        score = calculate_content_richness(container)
        scored_containers.append((container, score))
    
    # 选择得分最高的容器
    scored_containers.sort(key=lambda x: x[1], reverse=True)
    best_container = scored_containers[0][0]
    
    logger.info(f"页面主体容器得分: {scored_containers[0][1]}")
    return best_container

def calculate_content_richness(container):
    """计算容器的内容丰富度"""
    score = 0
    
    text_content = container.text_content().strip()
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
    
    # 检查图片数量
    images = container.xpath(".//img")
    if len(images) > 0:
        score += min(len(images) * 3, 20)
    
    # 检查结构化内容
    structured_elements = container.xpath(".//p | .//div[contains(@style, 'text-align')] | .//h1 | .//h2 | .//h3")
    if len(structured_elements) > 0:
        score += min(len(structured_elements) * 2, 25)
    
    return score

def exclude_local_header_footer(container):
    """在容器内部排除局部的header和footer"""
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
    """判断是否是局部的header或footer"""
    classes = element.get('class', '').lower()
    elem_id = element.get('id', '').lower()
    
    # 检查局部header/footer特征
    local_keywords = ['title', 'tit', 'head', 'foot', 'top', 'bottom', 'nav', 'menu']
    for keyword in local_keywords:
        if keyword in classes or keyword in elem_id:
            # 进一步检查是否真的是header/footer
            text_content = element.text_content().strip()
            if len(text_content) < 200:  # 内容较少，可能是标题或导航
                return True
    
    return False

def select_content_container(valid_children):
    """从有效子容器中选择最佳的内容容器"""
    if len(valid_children) == 1:
        return valid_children[0]
    
    # 计算每个容器的得分
    scored_containers = []
    for container in valid_children:
        score = calculate_final_score(container)
        scored_containers.append((container, score))
    
    # 选择得分最高的容器
    scored_containers.sort(key=lambda x: x[1], reverse=True)
    best_container = scored_containers[0][0]
    
    return best_container

def calculate_final_score(container):
    """计算最终容器得分"""
    score = 0
    
    text_content = container.text_content().strip()
    content_length = len(text_content)
    
    if content_length > 500:
        score += 30
    elif content_length > 200:
        score += 20
    elif content_length > 100:
        score += 15
    else:
        score += 5
    
    # 检查图片
    images = container.xpath(".//img")
    if len(images) > 0:
        score += min(len(images) * 4, 25)
    
    # 检查结构化内容
    styled_divs = container.xpath(".//div[contains(@style, 'text-align')]")
    paragraphs = container.xpath(".//p")
    
    structure_count = len(styled_divs) + len(paragraphs)
    if structure_count > 0:
        score += min(structure_count * 2, 20)
    
    # 检查类名特征
    classes = container.get('class', '').lower()
    elem_id = container.get('id', '').lower()
    
    content_keywords = ['content', 'article', 'detail', 'main', 'body', 'text', 'editor', 'con']
    for keyword in content_keywords:
        if keyword in classes or keyword in elem_id:
            score += 15
    
    return score

def find_main_content_area(containers):
    """在有效容器中找到主内容区域"""
    candidates = []
    
    for container in containers:
        score = calculate_main_content_score(container)
        if score > 0:
            candidates.append((container, score))
    
    if not candidates:
        return None
    
    # 选择得分最高的作为主内容区域
    candidates.sort(key=lambda x: x[1], reverse=True)
    main_area = candidates[0][0]
    
    logger.info(f"主内容区域得分: {candidates[0][1]}")
    return main_area

def calculate_main_content_score(container):
    """计算主内容区域得分"""
    score = 0
    
    text_content = container.text_content().strip()
    content_length = len(text_content)
    
    # 内容长度是主要指标
    if content_length > 500:
        score += 30
    elif content_length > 200:
        score += 20
    elif content_length > 100:
        score += 10
    else:
        return -5  # 内容太少
    
    # 检查是否包含丰富内容
    images = container.xpath(".//img")
    if len(images) > 0:
        score += min(len(images) * 2, 15)
    
    # 检查类名特征
    classes = container.get('class', '').lower()
    elem_id = container.get('id', '').lower()
    
    content_keywords = ['content', 'main', 'article', 'detail', 'body']
    for keyword in content_keywords:
        if keyword in classes or keyword in elem_id:
            score += 15
    
    return score


    
    # 检查类名
    classes = container.get('class', '').lower()
    if any(word in classes for word in ['content', 'article', 'detail', 'editor', 'text']):
        score += 15
    
    return score



def is_in_footer_area(element):
    """检查元素是否在footer区域"""
    current = element
    depth = 0
    while current is not None and depth < 10:  # 检查10层祖先
        classes = current.get('class', '').lower()
        elem_id = current.get('id', '').lower()
        tag_name = current.tag.lower()
        
        # 检查footer相关特征
        footer_indicators = [
            'footer', 'bottom', 'foot', 'end', 'copyright', 
            'links', 'sitemap', 'contact', 'about'
        ]
        
        for indicator in footer_indicators:
            if (indicator in classes or indicator in elem_id or 
                (tag_name == 'footer')):
                return True, f"发现footer特征: {indicator} (第{depth}层)"
        
        # 检查是否在页面底部区域（通过样式或位置判断）
        style = current.get('style', '').lower()
        if 'bottom' in style or 'fixed' in style:
            return True, f"发现底部样式 (第{depth}层)"
        
        current = current.getparent()
        depth += 1
    
    return False, ""

def find_list_container(page_tree):
    # 首先尝试使用改进的文章容器查找算法
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
        """计算容器作为目标列表的得分 - 第一轮严格过滤首部尾部"""
        score = 0
        debug_info = []
        
        # 获取容器的基本信息
        classes = container.get('class', '').lower()
        elem_id = container.get('id', '').lower()
        role = container.get('role', '').lower()
        tag_name = container.tag.lower()
        text_content = container.text_content().lower()
        
        # 第一轮过滤：根据内容特征直接排除首部和尾部容器
        # 1. 检查首部特征内容
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
        
        # 如果包含多个首部关键词，严重减分
        if header_content_count >= 2:
            score -= 300  # 极严重减分，基本排除
            debug_info.append(f"首部内容特征: -300 (发现{header_content_count}个首部关键词)")
        
        # 2. 检查尾部特征内容
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
        
        # 如果包含多个尾部关键词，严重减分
        if footer_content_count >= 2:
            score -= 300  # 极严重减分，基本排除
            debug_info.append(f"尾部内容特征: -300 (发现{footer_content_count}个尾部关键词)")
        
        # 3. 检查结构特征 - footer/header标签和类名
        footer_structure_indicators = ['footer', 'foot', 'bottom', 'end', 'copyright', 'links', 'sitemap']
        for indicator in footer_structure_indicators:
            if (indicator in classes or indicator in elem_id or 
                indicator in role or tag_name == 'footer'):
                score -= 250  # 极严重减分
                debug_info.append(f"Footer结构特征: -250 (发现'{indicator}')")
        
        # 4. 检查header/nav结构特征
        header_structure_indicators = ['header', 'nav', 'navigation', 'menu', 'topbar', 'banner', 'menubar']
        for indicator in header_structure_indicators:
            if (indicator in classes or indicator in elem_id or 
                indicator in role or tag_name in ['header', 'nav','menu']):
                score -= 200  # 严重减分
                debug_info.append(f"Header结构特征: -200 (发现'{indicator}')")
        
        # 5. 检查祖先元素的负面特征（但权重降低，因为第一轮已经过滤了大部分）
        current = container
        depth = 0
        while current is not None and depth < 5:  # 减少检查层级
            parent_classes = current.get('class', '').lower()
            parent_id = current.get('id', '').lower()
            parent_tag = current.tag.lower()
            
            # 检查祖先的footer特征
            for indicator in footer_structure_indicators:
                if (indicator in parent_classes or indicator in parent_id or parent_tag == 'footer'):
                    penalty = max(60 - depth * 10, 15)  # 减少祖先特征的权重
                    score -= penalty
                    debug_info.append(f"祖先Footer: -{penalty} (第{depth}层'{indicator}')")
            
            # 检查祖先的header/nav特征
            for indicator in header_structure_indicators:
                if (indicator in parent_classes or indicator in parent_id or parent_tag in ['header', 'nav']):
                    penalty = max(50 - depth * 8, 12)  # 减少祖先特征的权重
                    score -= penalty
                    debug_info.append(f"祖先Header: -{penalty} (第{depth}层'{indicator}')")
            
            current = current.getparent()
            depth += 1
        
        # 如果已经是严重负分，直接返回，不需要继续计算
        if score < -150:
            return score
        
        # 6. 正面特征评分 - 专注于内容质量
        # 检查时间特征（强正面特征）
        precise_time_patterns = [
            r'\d{4}-\d{2}-\d{2}',  # YYYY-MM-DD
            r'\d{4}年\d{1,2}月\d{1,2}日',  # 完整的中文日期
            r'\d{4}/\d{1,2}/\d{1,2}',  # YYYY/MM/DD
            r'发布时间', r'更新日期', r'发布日期', r'创建时间'
        ]
        
        precise_matches = 0
        for pattern in precise_time_patterns:
            matches = len(re.findall(pattern, text_content))
            precise_matches += matches
        
        if precise_matches > 0:
            time_score = min(precise_matches * 30, 90)  # 增加时间特征权重
            score += time_score
            debug_info.append(f"时间特征: +{time_score} ({precise_matches}个匹配)")
        
        # 7. 检查内容长度和质量
        items = container.xpath(".//*[self::li or self::tr or self::article or self::div[contains(@class, 'item')]]")
        if items:
            total_length = sum(len(item.text_content().strip()) for item in items)
            avg_length = total_length / len(items) if items else 0
            
            if avg_length > 150:
                score += 40  # 增加长内容的权重
                debug_info.append(f"文本长度: +40 (平均{avg_length:.1f}字符)")
            elif avg_length > 80:
                score += 30
                debug_info.append(f"文本长度: +30 (平均{avg_length:.1f}字符)")
            elif avg_length > 40:
                score += 20
                debug_info.append(f"文本长度: +20 (平均{avg_length:.1f}字符)")
            elif avg_length < 20:  # 文本太短，可能是导航
                score -= 20
                debug_info.append(f"文本长度: -20 (平均{avg_length:.1f}字符，太短)")
        
        # 8. 检查正面结构特征
        strong_positive_indicators = ['content', 'main', 'news', 'article', 'data', 'info', 'detail', 'result', 'list']
        positive_score = 0
        for indicator in strong_positive_indicators:
            if indicator in classes or indicator in elem_id:
                positive_score += 25  # 增加正面特征权重
                debug_info.append(f"正面特征: +25 ('{indicator}')")
        
        score += min(positive_score, 75)  # 限制正面特征的最大加分
        
        # 9. 检查内容多样性（图片、链接等）
        images = container.xpath(".//img")
        links = container.xpath(".//a[@href]")
        
        if len(images) > 0:
            image_score = min(len(images) * 3, 20)
            score += image_score
            debug_info.append(f"图片内容: +{image_score} ({len(images)}张图片)")
        
        if len(links) > 5:  # 有足够的链接说明是内容区域
            link_score = min(len(links) * 2, 30)
            score += link_score
            debug_info.append(f"链接内容: +{link_score} ({len(links)}个链接)")
        
        # 10. 最后检查：避免导航类内容（但权重降低，因为第一轮已经过滤了大部分）
        if items and len(items) > 2:
            # 只检查明显的导航词汇，减少误判
            strong_nav_words = [
                '登录', '注册', '首页', '主页', '无障碍', '办事', '无障碍浏览','打印','收藏','机构概况','在线服务','互动交流',
                '走进', '移动版', '手机版', '导航', '菜单', '搜索', '市政府',
                'login', 'register', 'home', 'menu', 'search', 'nav'
            ]
            nav_word_count = 0
            
            for item in items[:8]:  # 减少检查的项目数
                item_text = item.text_content().strip().lower()
                for nav_word in strong_nav_words:
                    if nav_word in item_text:
                        nav_word_count += 1
                        break
            
            checked_items = min(len(items), 8)
            if nav_word_count > checked_items * 0.4:  # 提高阈值，减少误判
                nav_penalty = 30  # 减少导航词汇的减分
                score -= nav_penalty
                debug_info.append(f"导航词汇: -{nav_penalty} ({nav_word_count}/{checked_items}个)")
        
        # 输出调试信息
        container_info = f"标签:{tag_name}, 类名:{classes[:30]}{'...' if len(classes) > 30 else ''}"
        if elem_id:
            container_info += f", ID:{elem_id[:20]}{'...' if len(elem_id) > 20 else ''}"
        
        logger.info(f"容器评分: {score} - {container_info}")
        for info in debug_info:  # 显示更多调试信息
            logger.info(f"  {info}")
        
        return score
    
    # 第一层：找到所有可能的列表项
    all_items = []
    for selector in list_selectors:
        items = page_tree.xpath(selector)
        all_items.extend(items)
    
    if not all_items:
        return None
    
    # 按照父元素分组，找到包含列表项的父元素
    parent_counts = {}
    for item in all_items:
        parent = item.getparent()
        if parent is not None:
            if parent not in parent_counts:
                parent_counts[parent] = 0
            parent_counts[parent] += 1
    
    if not parent_counts:
        return None
    
    # 筛选候选容器：至少包含3个列表项
    candidate_containers = [(parent, count) for parent, count in parent_counts.items() if count >= 3]
    
    # 如果没有符合条件的容器，降低门槛到2个列表项
    if not candidate_containers:
        candidate_containers = [(parent, count) for parent, count in parent_counts.items() if count >= 2]
    
    # 如果还是没有，返回包含最多列表项的容器
    if not candidate_containers:
        return max(parent_counts.items(), key=lambda x: x[1])[0]
    
    # 对候选容器进行评分并排序
    scored_containers = []
    for container, count in candidate_containers:
        score = calculate_container_score(container)
        
        # 额外检查：如果容器在footer区域，严重减分
        is_footer, footer_msg = is_in_footer_area(container)
        ancestry_penalty = 0
        
        if is_footer:
            ancestry_penalty += 50  # footer区域严重减分
        
        # 检查其他负面祖先特征 - 但权重降低，因为第一轮已经过滤了大部分
        def check_negative_ancestry(element):
            """检查元素及其祖先的负面特征"""
            penalty = 0
            current = element
            depth = 0
            while current is not None and depth < 4:  # 减少检查层级
                classes = current.get('class', '').lower()
                elem_id = current.get('id', '').lower()
                text_content = current.text_content().lower()
                
                # 检查结构特征
                negative_keywords = ['nav', 'menu', 'sidebar', 'header', 'topbar', 'navigation', 'head']
                for keyword in negative_keywords:
                    if keyword in classes or keyword in elem_id:
                        penalty += 20  # 减少祖先特征的权重
                
                # 检查内容特征（只在前2层检查）
                if depth < 2:
                    footer_content_keywords = ['网站说明', '网站标识码', '版权所有', '备案号']
                    header_content_keywords = ['登录', '注册', '首页', '无障碍']
                    
                    content_penalty = 0
                    for keyword in footer_content_keywords + header_content_keywords:
                        if keyword in text_content:
                            content_penalty += 15
                    
                    if content_penalty > 30:  # 如果包含多个关键词
                        penalty += content_penalty
                
                current = current.getparent()
                depth += 1
            return penalty
        
        ancestry_penalty += check_negative_ancestry(container)
        #最终分数
        final_score = score - ancestry_penalty
        
        scored_containers.append((container, final_score, count))
    
    # 按分数排序，但优先考虑分数而不是数量
    scored_containers.sort(key=lambda x: x[1], reverse=True)
    
    # 严格过滤负分容器 - 提高阈值，更严格地排除首部尾部
    positive_scored = [sc for sc in scored_containers if sc[1] > 0]  # 只接受正分容器
    
    if positive_scored:
        # 选择得分最高的正分容器
        best_container = positive_scored[0][0]
        max_items = parent_counts[best_container]
    else:
        # 如果没有正分容器，尝试稍微宽松的阈值
        moderate_scored = [sc for sc in scored_containers if sc[1] > -50]
        
        if moderate_scored:
            best_container = moderate_scored[0][0]
            max_items = parent_counts[best_container]
        else:
            # 最后手段：选择得分最高的（但很可能不理想）
            best_container = scored_containers[0][0]
            max_items = parent_counts[best_container]
    
    # 逐层向上搜索优化容器
    current_container = best_container
    while True:
        parent = current_container.getparent()
        if parent is None or parent.tag == 'html':
            break
        
        # 检查父级元素是否包含footer等负面特征 - 更严格的检查
        def has_negative_ancestor(element):
            """检查元素的祖先是否包含负面特征 - 包括内容特征"""
            current = element
            depth = 0
            while current is not None and depth < 3:  # 检查3层祖先
                parent_classes = current.get('class', '').lower()
                parent_id = current.get('id', '').lower()
                parent_tag = current.tag.lower()
                parent_text = current.text_content().lower()
                
                # 检查结构负面关键词
                structure_negative = ['footer', 'nav', 'menu', 'sidebar', 'header', 'topbar', 'navigation', 'foot', 'head']
                for keyword in structure_negative:
                    if (keyword in parent_classes or keyword in parent_id or parent_tag in ['footer', 'header', 'nav']):
                        return True
                
                # 检查内容负面特征（只在前2层检查，避免过度检查）
                if depth < 2:
                    # 首部内容特征
                    header_content = ['登录', '注册', '首页', '主页', '无障碍', '办事', '走进']
                    header_count = sum(1 for word in header_content if word in parent_text)
                    
                    # 尾部内容特征
                    footer_content = ['网站说明', '网站标识码', '版权所有', '备案号', 'icp', '主办单位', '承办单位']
                    footer_count = sum(1 for word in footer_content if word in parent_text)
                    
                    # 如果包含多个首部或尾部关键词，认为是负面祖先
                    if header_count >= 2:
                        return True
                    if footer_count >= 2:
                        return True
                
                current = current.getparent()
                depth += 1
            return False
        
        # 如果父元素或其祖先包含负面特征，停止向上搜索
        if has_negative_ancestor(parent):
            logger.info("父级包含负面特征，停止向上搜索")
            break
            
        # 计算父元素中的列表项数量
        parent_items = count_list_items(parent)
        
        # 检查父元素是否更适合作为容器
        parent_score = calculate_container_score(parent)
        current_score = calculate_container_score(current_container)
        
        logger.info(f"比较得分: 当前={current_score}, 父级={parent_score}")
        logger.info(f"项目数量: 当前={max_items}, 父级={parent_items}")
        
        should_upgrade = False
        
        # 首先检查父级是否有严重的负面特征
        if parent_score < -50:
            logger.info(f"父级得分过低({parent_score})，跳过升级")
        else:
            # 条件1：父级得分明显更高且为正分
            if parent_score > current_score + 15 and parent_score > 10:
                should_upgrade = True
                logger.info("父级得分明显更高且为正分，升级")
            
            # 条件2：父级得分相近且为正分，包含合理数量的项目
            elif (parent_score >= current_score - 3 and 
                  parent_score > 5 and  # 要求父级必须是正分
                  parent_items <= max_items * 2 and  # 更严格的项目数量限制
                  parent_items >= max_items):
                should_upgrade = True
                logger.info("父级得分相近且为正分，升级")
            
            # 条件3：当前容器项目太少，父级有合理数量且得分不错
            elif (max_items < 4 and 
                  parent_items >= max_items and 
                  parent_items <= 15 and 
                  parent_score > 0):  # 要求父级必须是正分
                should_upgrade = True
                logger.info("当前容器项目太少，升级到正分父级")
        
        if should_upgrade:
            current_container = parent
            max_items = parent_items
            logger.info("升级到父级容器")
        else:
            logger.info("保持当前容器")
            break
        
        # 安全检查：如果父级项目数量过多，停止
        if parent_items > 50:
            logger.info(f"父级项目数量过多({parent_items})，停止向上搜索")
            break
    
    # 最终验证：确保选择的容器包含足够的列表项且不是首部尾部
    final_items = count_list_items(current_container)
    final_score = calculate_container_score(current_container)
    logger.info(f"最终容器包含 {final_items} 个列表项，得分: {final_score}")
    
    # 如果最终容器项目太少且得分不好，尝试向上找一层
    if final_items < 4 or final_score < -10:
        parent = current_container.getparent()
        if parent is not None and parent.tag != 'html':
            parent_items = count_list_items(parent)
            parent_score = calculate_container_score(parent)
            
            # 更严格的条件：父级必须有更多项目且得分为正分
            if (parent_items > final_items and 
                parent_score > 0 and  # 要求正分
                parent_items <= 30):  # 避免选择过大的容器
                logger.info(f"最终调整：选择正分父级容器 (项目数: {parent_items}, 得分: {parent_score})")
                current_container = parent
            else:
                logger.info(f"父级不符合条件 (项目数: {parent_items}, 得分: {parent_score})，保持当前选择")
    
    return current_container
def generate_xpath(element):
    if element is None:
        return None

    tag = element.tag

    # 1. 优先使用ID（如果存在且不是干扰特征）
    elem_id = element.get('id')
    if elem_id and not is_interference_identifier(elem_id):
        return f"//{tag}[@id='{elem_id}']"

    # 2. 使用类名（过滤干扰类名）
    # classes = element.get('class')
    # if classes:
    #     class_list = [cls.strip() for cls in classes.split() if cls.strip()]
    #     # 过滤掉干扰类名
    #     clean_classes = [cls for cls in class_list if not is_interference_identifier(cls)]
    #     if clean_classes:
    #         # 选择最长的干净类名
    #         longest_class = max(clean_classes, key=len)
    #         return f"//{tag}[contains(concat(' ', normalize-space(@class), ' '), ' {longest_class} ')]"
    classes = element.get('class')
    if classes:
        # 使用完整的class值，不进行过滤处理
        return f"//{tag}[@class='{classes}']"

    # 3. 使用其他属性（如 aria-label 等）
    for attr in ['aria-label', 'role', 'data-testid', 'data-role']:
        attr_value = element.get(attr)
        if attr_value and not is_interference_identifier(attr_value):
            return f"//{tag}[@{attr}='{attr_value}']"

    # 4. 尝试找到最近的有干净标识符的祖先
    def find_closest_clean_identifier(el):
        parent = el.getparent()
        while parent is not None and parent.tag != 'html':
            # 检查ID
            parent_id = parent.get('id')
            if parent_id and not is_interference_identifier(parent_id):
                return parent
            
            # 检查类名
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
        # 生成祖先的 XPath
        ancestor_xpath = generate_xpath(ancestor)
        if ancestor_xpath:
            # 生成从祖先到当前元素的相对路径
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

    # 5. 基于位置的 XPath（最后手段）
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
    """判断标识符是否包含干扰特征"""
    if not identifier:
        return False
    
    identifier_lower = identifier.lower()
    
    # 干扰关键词
    interference_keywords = [
        'header', 'footer', 'nav', 'navigation', 'menu', 'menubar',
        'topbar', 'bottom', 'sidebar', 'aside', 'banner', 'ad'
    ]
    
    for keyword in interference_keywords:
        if keyword in identifier_lower:
            return True
    
    return False

# 移除了验证函数，现在只需要核心的HTML处理


# 移除了所有浏览器和文件处理相关的函数


# FastAPI路由
@app.get("/")
async def root():
    """根路径，返回API信息"""
    return {
        "message": "HTML to Markdown Content Extractor API",
        "version": "2.0.0",
        "endpoints": {
            "/extract": "POST - Extract main content from HTML and convert to Markdown",
            "/health": "GET - Health check"
        }
    }

@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }

@app.post("/extract", response_model=MarkdownOutput)
async def extract_html_to_markdown(input_data: HTMLInput):
    """
    从HTML内容中提取正文并转换为Markdown格式
    
    Args:
        input_data: 包含HTML内容的输入数据
        
    Returns:
        MarkdownOutput: 包含以下字段的响应
            - markdown_content: 提取的Markdown格式内容
            - html_content: 提取的HTML格式内容（已清理script/style标签）
            - xpath: 定位到内容容器的XPath表达式
            - status: 处理状态 (success/failed)
    """
    try:
        if not input_data.html_content.strip():
            raise HTTPException(status_code=400, detail="HTML内容不能为空")
        
        logger.info("开始处理HTML内容提取")
        
        # 提取内容并转换为Markdown
        result = extract_content_to_markdown(input_data.html_content)
        
        if result['status'] == 'failed':
            raise HTTPException(status_code=422, detail="无法从HTML中提取有效内容")
        
        return MarkdownOutput(
            markdown_content=result['markdown_content'],
            html_content=result['html_content'],
            xpath=result['xpath'],
            status=result['status']
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

import os
import glob

# 启动服务器的函数
def start_server(host: str = "0.0.0.0", port: int = 8321):
    """启动FastAPI服务器"""
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    # 可以选择运行原有的文件处理逻辑或启动API服务器
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "api":
        # 启动API服务器
        print("启动HTML to Markdown API服务器...")
        print("API文档: http://localhost:8321/docs")
        print("健康检查: http://localhost:8321/health")
        start_server()
    # else:
    #     # 原有的文件处理逻辑（保留向后兼容）
    #     try:
    #         input_file = "test.yml"    # 输入文件路径
    #         output_file = "testout.yml"  # 输出文件路径
            
    #         process_yml_file(input_file, output_file)

            # input_folder = "waitprocess"
            # output_folder = "processed"  
            
            # if not os.path.exists(output_folder):
            #     os.makedirs(output_folder)
            
            # files = glob.glob(os.path.join(input_folder, "*.yml"))
            
            # for input_file in files:
            #     base_name = os.path.basename(input_file)  
            #     output_file = os.path.join(output_folder, base_name)
            #     process_yml_file(input_file, output_file)
        # finally:
        #     driver_pool.close_all()


# version1.0 

# 一个页面中，存在k个列表，假定k=3，有三个列表，列表1为导航栏，里面有8个列表项，列表2为侧边栏，里面有5个列表项，列表3是事项列表，里面有7个列表项，
# 此时，我的代码会把列表1作为目标获取，但实际情况应该是列表3才是正确的，这怎么办呢，
# 目前对于目标列表3，可能存在以下特点：里面往往存在时间字符串，并且有些页面中的文字的长度是大于列表1和列表2的。
# 除此之外，对于列表1，也有以下特点：当我们误获取这个列表1的时候，会去处理组装他的xpath，这个xpath里面往往是存在nav三个字母的，
# 在我的观察下，大部分情况中，只要xpath里面包含nav，那就很大可能说明获取失败了，没有获取到列表3，而是获取到了列表1

# 对于js页面，name中名称一定要准确，并且！name要尽量要少一点，比如“法定主动公开内容”，这个就写“法定”即可，这俩字有代表性，不能写“内容”这俩字，没有任何的代表性


# version2.0
# 2025.8.22
# 修改部分算法的逻辑，可以提取正文所在容器，而不是v1.0中提取列表，目前算法用于定位页面的主体内容，通过不断的去排除头部导航和底部footer来逐渐的定位主体。但是，对于页面中内容是一大串的文字，或者是图片，这种情况下密度算法将会失效，我们需要尽可能的排除主HTML中head和footer（就是页面的导航栏和底部栏，这两个里面可能存在大量的列表或者一大串的文字）
# 获取到的次HTML即为排除了干扰项的HTML内容，我们需要的container可能就存在于此，对于这个次级HTML，我们需要再一次的进行过滤，排除里面的header和footer，然后逐步缩小，但是不要精确，因为过于精确的获取容器会导致出现疏漏。

# 对于算法的进一步修改，需要判断出一个合理的权重，即扣分标准。首先！一定是扣分的居多，加分的少，对于可能是底部或者首部的内容，要大量的减分，应该算法的主要思路就是排除干扰项！
