#!/usr/bin/env python3
"""
IBM Think to EPUB Converter
Converts IBM Think guides to EPUB format.
"""

import re
import click
import requests
from bs4 import BeautifulSoup
from ebooklib import epub
from urllib.parse import urljoin, urlparse
import time
import hashlib
from io import BytesIO
from typing import List, Dict, Optional, Set, Tuple
from html2image import Html2Image


class IBMThinkScraper:
    """Scraper for IBM Think guide pages."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.downloaded_images: Dict[str, str] = {}  # URL -> local filename

    def get_page(self, url: str) -> Optional[BeautifulSoup]:
        """Fetch and parse a page."""
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return BeautifulSoup(response.content, 'lxml')
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            return None

    def extract_toc_from_sidebar(self, soup: BeautifulSoup) -> List[Dict[str, str]]:
        """Extract table of contents from sidebar navigation."""
        toc_items = []

        # Find the sidebar navigation with class 'cmp-side-navigation'
        sidebar = soup.find('nav', class_='cmp-side-navigation')

        if not sidebar:
            print("Warning: No sidebar found with class 'cmp-side-navigation'")
            # Fallback: try to find any navigation sidebar
            sidebar = soup.find_all(
                ['nav', 'aside'], class_=re.compile(r'sidebar|nav|toc'))

        if sidebar:
            # Parse the hierarchical structure
            toc_items = self._parse_navigation_level(sidebar, level=0)

        return toc_items

    def _parse_navigation_level(self, container, level: int = 0) -> List[Dict]:
        """Recursively parse navigation levels."""
        items = []

        # Find the appropriate level class
        level_class = f'cmp-side-navigation__level{level}'
        ul_element = container.find('ul', class_=level_class)

        if not ul_element:
            # For level 0, look directly in the container
            if level == 0:
                ul_element = container.find('ul', class_=level_class)
            if not ul_element:
                return items

        for li in ul_element.find_all('li', class_=f'cmp-side-navigation__section--level{level}', recursive=False):
            item_data = self._parse_navigation_item(li, level)
            if item_data:
                items.append(item_data)

        return items

    def _parse_navigation_item(self, li_element, level: int) -> Optional[Dict]:
        """Parse a single navigation item."""
        # Check if this is a direct link
        link = li_element.find(
            'a', class_=f'cmp-side-navigation__item--level{level}', recursive=False)
        if link:
            href = link.get('href', '')
            title = link.get_text(strip=True)
            if href and title:
                absolute_url = urljoin(self.base_url, href)
                return {
                    'title': title,
                    'url': absolute_url,
                    'href': href,
                    'type': 'link',
                    'level': level
                }

        # Check if this is a collapsible section (heading with children)
        collapsible = li_element.find(
            'span', class_=f'cmp-side-navigation__item--collapsible', recursive=False)
        if collapsible:
            # Get the text content, excluding SVG elements and "Caret right" text
            title_parts = []
            for element in collapsible.children:
                if element.name != 'svg':
                    text = element.get_text(strip=True) if hasattr(
                        element, 'get_text') else str(element).strip()
                    # Skip "Caret right" text that appears before actual titles
                    if text and text != 'Caret right':
                        title_parts.append(text)
            title = ' '.join(title_parts).strip()

            if title:
                # Remove extra whitespace and clean up
                title = re.sub(r'\s+', ' ', title).strip()

                # Parse children
                children = self._parse_navigation_level(li_element, level + 1)

                return {
                    'title': title,
                    'type': 'section',
                    'level': level,
                    'children': children
                }

        return None

    def _flatten_toc_structure(self, toc_structure: List[Dict]) -> List[Dict[str, str]]:
        """Flatten hierarchical TOC structure into a list of links for processing."""
        flattened = []

        def _flatten_recursive(items):
            for item in items:
                if item['type'] == 'link':
                    flattened.append(item)
                elif item['type'] == 'section' and 'children' in item:
                    _flatten_recursive(item['children'])

        _flatten_recursive(toc_structure)
        return flattened

    def _limit_toc_structure(self, toc_structure: List[Dict], processed_urls: Set[str]) -> List[Dict]:
        """Create a limited TOC structure containing only processed URLs."""
        def _limit_recursive(items):
            result = []
            for item in items:
                if item['type'] == 'link':
                    if item['url'] in processed_urls:
                        result.append(item)
                elif item['type'] == 'section' and 'children' in item:
                    limited_children = _limit_recursive(item['children'])
                    if limited_children:  # Only include section if it has children
                        limited_item = item.copy()
                        limited_item['children'] = limited_children
                        result.append(limited_item)
            return result

        return _limit_recursive(toc_structure)

    def extract_content(self, soup: BeautifulSoup) -> str:
        """Extract main content from body-article-8 div only."""
        content_parts = []

        # Find only the div with class 'body-article-8'
        body_articles = soup.find_all('div', class_='body-article-8')

        if not body_articles:
            print("Warning: No body-article-8 div found, trying alternative selectors")
            # Fallback to common content containers
            body_articles = soup.find_all(['article', 'main', 'div'],
                                          class_=re.compile(r'content|article|body'))

        for article in body_articles:
            # Remove unwanted elements by class
            for unwanted_class in ['article-content-slot', 'share-module', 'author-signature']:
                for elem in article.find_all(class_=re.compile(unwanted_class)):
                    elem.decompose()

            # Remove unwanted elements by tag
            for unwanted in article.find_all(['script', 'style', 'nav', 'footer',
                                             'aside', 'iframe', 'noscript']):
                unwanted.decompose()

            # Remove elements with common ad/tracking classes
            for ad_class in ['advertisement', 'ad-', 'tracking', 'social-share',
                             'cookie-', 'banner', 'popup', 'modal']:
                for elem in article.find_all(class_=re.compile(ad_class)):
                    elem.decompose()

            content_parts.append(str(article))

        return '\n'.join(content_parts) if content_parts else str(soup.find('body') or '')

    def clean_links(self, soup: BeautifulSoup, chapter_map: Dict[str, epub.EpubHtml] = None) -> BeautifulSoup:
        """Remove or fix problematic links, and rewrite internal links to point within the EPUB."""
        # Remove links that point to missing resources or external sites
        for link in soup.find_all('a', href=True):
            href = link.get('href', '')
            original_href = href

            # Handle absolute URLs that might be internal to IBM Think
            if href.startswith('http') and 'ibm.com/think' in href:
                # This is an internal IBM Think link - check if we have it in our chapter map
                if chapter_map and href in chapter_map:
                    # Rewrite to point to the EPUB chapter
                    chapter = chapter_map[href]
                    link['href'] = chapter.file_name
                    continue

            # Remove links to missing resources or external non-http links
            if href.startswith('javascript:') or \
               href.startswith('mailto:') or \
               'adobe-cms' in href or \
               href.endswith('.html') and not href.startswith('http'):
                # Convert link to plain text
                link.unwrap()
            # Remove fragment-only links if they don't have valid targets
            elif href.startswith('#'):
                target_id = href[1:]
                if not soup.find(id=target_id):
                    link.unwrap()

        return soup

    def download_image(self, img_url: str) -> Optional[Tuple[bytes, str]]:
        """Download an image and return its content and extension."""
        try:
            response = self.session.get(img_url, timeout=10)
            response.raise_for_status()

            # Determine image type from content-type or URL
            content_type = response.headers.get('content-type', '')
            if 'image/jpeg' in content_type or img_url.endswith(('.jpg', '.jpeg')):
                ext = 'jpg'
                media_type = 'image/jpeg'
            elif 'image/png' in content_type or img_url.endswith('.png'):
                ext = 'png'
                media_type = 'image/png'
            elif 'image/gif' in content_type or img_url.endswith('.gif'):
                ext = 'gif'
                media_type = 'image/gif'
            elif 'image/svg' in content_type or img_url.endswith('.svg'):
                ext = 'svg'
                media_type = 'image/svg+xml'
            elif 'image/webp' in content_type or img_url.endswith('.webp'):
                ext = 'webp'
                media_type = 'image/webp'
            else:
                ext = 'jpg'  # default
                media_type = 'image/jpeg'

            return response.content, ext, media_type
        except Exception as e:
            print(f"  Warning: Failed to download image {img_url}: {e}")
            return None

    def process_images(self, soup: BeautifulSoup) -> BeautifulSoup:
        """Download images and update their src attributes."""
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if not src:
                continue

            # Make absolute URL
            img_url = urljoin(self.base_url, src)

            # Skip if already downloaded
            if img_url in self.downloaded_images:
                img['src'] = self.downloaded_images[img_url]
                # Remove srcset attribute (can contain data URIs that conflict)
                if img.has_attr('srcset'):
                    del img['srcset']
                continue

            # Download image
            result = self.download_image(img_url)
            if result:
                content, ext, media_type = result
                # Generate unique filename
                img_hash = hashlib.md5(img_url.encode()).hexdigest()[:12]
                img_filename = f'images/img_{img_hash}.{ext}'

                # Store for later addition to EPUB
                self.downloaded_images[img_url] = {
                    'filename': img_filename,
                    'content': content,
                    'media_type': media_type
                }

                # Update src in HTML and remove problematic attributes
                img['src'] = img_filename
                # Remove srcset attribute (can contain data URIs that conflict with src)
                if img.has_attr('srcset'):
                    del img['srcset']
                # Remove loading attribute (not well-supported in EPUB readers)
                if img.has_attr('loading'):
                    del img['loading']

        return soup

    def clean_html(self, html: str) -> str:
        """Clean and normalize HTML content for EPUB compliance."""
        soup = BeautifulSoup(html, 'lxml')

        # Remove SVG elements entirely (they cause validation issues)
        for svg in soup.find_all('svg'):
            svg.decompose()

        # Handle MathML elements - add proper namespace for EPUB3 compatibility
        for math_elem in soup.find_all('math'):
            # Ensure math elements have the proper MathML namespace
            if not math_elem.get('xmlns'):
                math_elem['xmlns'] = 'http://www.w3.org/1998/Math/MathML'
            
            # Remove any empty or malformed MathML child elements
            for child in math_elem.find_all():
                # Remove elements that are empty and have no attributes
                if not child.get_text(strip=True) and not child.attrs:
                    child.decompose()
                # Wrap bare text nodes in MathML elements with proper tags
                elif child.name and not child.find_all():
                    # If element is empty, remove it
                    if not child.get_text(strip=True):
                        child.decompose()
        
        # Remove iframes and embedded content (not allowed in EPUB)
        for iframe in soup.find_all('iframe'):
            iframe.decompose()
        
        # Remove video and audio elements with remote sources
        for media in soup.find_all(['video', 'audio']):
            media.decompose()
        
        # Remove script tags
        for script in soup.find_all('script'):
            script.decompose()

        # Convert cds-code-snippet elements to proper code elements
        for code_snippet in soup.find_all('cds-code-snippet'):
            # Get the content
            content = code_snippet.get_text(strip=True)
            snippet_type = code_snippet.get('type', 'inline')

            # Create appropriate code element
            if snippet_type == 'multi':
                # Multi-line code should use pre and code
                pre_tag = soup.new_tag('pre')
                code_tag = soup.new_tag('code')
                code_tag.string = content
                pre_tag.append(code_tag)
                code_snippet.replace_with(pre_tag)
            else:
                # Inline code
                code_tag = soup.new_tag('code')
                code_tag.string = content
                code_snippet.replace_with(code_tag)

        # Remove deprecated table attributes
        for table in soup.find_all('table'):
            if table.has_attr('cellpadding'):
                del table['cellpadding']
            if table.has_attr('cellspacing'):
                del table['cellspacing']
            if table.has_attr('border'):
                del table['border']

        # Remove <picture> wrappers and <source> tags that interfere with EPUB rendering
        # EPUB readers often don't handle <picture> elements well, especially with placeholder data URIs
        for picture in soup.find_all('picture'):
            # Find the img tag inside the picture element
            img = picture.find('img')
            if img:
                # Remove all source tags
                for source in picture.find_all('source'):
                    source.decompose()
                # Replace the picture element with just the img tag
                picture.replace_with(img)
            else:
                # No img tag found, remove the entire picture element
                picture.decompose()

        # Fix invalid image src attributes (remove data URIs, invalid URLs, and remote resources)
        for img in soup.find_all('img'):
            src = img.get('src', '')
            # Remove images with data URIs, invalid URLs, or external HTTP/HTTPS references
            if src.startswith('data:') or \
               src.startswith('http://') or \
               src.startswith('https://') or \
               src.startswith('//') or \
               '%%' in src or \
               (src and '%' in src and not src.startswith('http')):
                img.decompose()
                continue
        
        # Remove links to external stylesheets (remote resources)
        for link in soup.find_all('link', href=True):
            href = link.get('href', '')
            if href.startswith('http://') or href.startswith('https://') or href.startswith('//'):
                link.decompose()
        
        # Remove elements with src/href pointing to remote resources
        for tag in soup.find_all(attrs={'src': True}):
            src = tag.get('src', '')
            if src.startswith('http://') or src.startswith('https://') or src.startswith('//'):
                tag.decompose()

        # Fix nested heading issues
        # 1. First, unwrap headings that are nested within other headings
        for heading_level in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            for parent_heading in soup.find_all(heading_level):
                # Find any nested headings
                for nested_heading in parent_heading.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    # Replace parent with the nested heading's content
                    parent_heading.replace_with(nested_heading)
                    break  # Only handle one nested heading per parent

        # 2. Move headings out of inline/text-only elements and fix malformed structures
        for inline_tag in soup.find_all(['p', 'span', 'a', 'strong', 'em', 'i', 'b', 'u']):
            # Find any heading tags inside inline elements
            for heading in inline_tag.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                # Extract the heading and insert it before the parent
                heading.extract()
                inline_tag.insert_before(heading)

        # 3. Fix headings that contain lists - move lists outside headings
        for heading in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            # Find any lists inside headings
            for list_elem in heading.find_all(['ul', 'ol']):
                # Move the list after the heading
                list_elem.extract()
                heading.insert_after(list_elem)

        # Remove empty paragraphs and divs
        for tag in soup.find_all(['p', 'div', 'span']):
            if not tag.get_text(strip=True) and not tag.find('img'):
                tag.decompose()

        # Remove elements with invalid attributes
        invalid_attrs = [
            'slot', 'viewbox', 'driverlocation', 'cta-type', 'icon-placement',
            'data-cmp-hook-image', 'data-cmp-is', 'data-cmp-widths', 'data-cmp-dmimage',
            'data-cmp-src', 'data-asset-id', 'data-cmp-filereference', 'data-cmp-data-layer',
            'data-cmp-aspectratio', 'data-cmp-aspectratio-max', 'data-cmp-aspectratio-xl',
            'data-cmp-aspectratio-md', 'data-cmp-aspectratio-lg', 'data-cmp-aspectratio-sm'
        ]
        
        for tag in soup.find_all(True):
            # Remove invalid attributes
            for attr in list(tag.attrs.keys()):
                # Remove specific invalid attributes
                if attr in invalid_attrs:
                    del tag[attr]
                # Remove any data-* attributes that start with specific prefixes
                elif attr.startswith('data-cmp') or attr.startswith('data-asset'):
                    del tag[attr]

        return str(soup)


class EPUBGenerator:
    """Generate EPUB files from scraped content."""

    def __init__(self, title: str, author: str = "IBM Think"):
        self.book = epub.EpubBook()
        self.book.set_title(title)
        self.book.set_language('en')
        self.book.add_author(author)
        self.chapters = []
        self.toc_structure = []  # Will hold the hierarchical TOC structure
        self.chapter_map = {}  # Map URLs to chapter objects for TOC building

    def add_chapter(self, title: str, content: str, filename: str, url: str = None):
        """Add a chapter to the EPUB book."""
        chapter = epub.EpubHtml(
            title=title,
            file_name=filename,
            lang='en'
        )
        chapter.content = f'<h1>{title}</h1>\n{content}'

        # Check if the content contains MathML and set the property if it does
        if '<math' in content:
            chapter.add_item(epub.EpubItem(
                uid='mathml',
                file_name='',
                media_type='',
                content=''
            ))
            # Set the mathml property in the chapter's properties
            chapter.properties.append('mathml')

        self.book.add_item(chapter)
        self.chapters.append(chapter)

        # Store mapping for TOC building
        if url:
            self.chapter_map[url] = chapter

        return chapter

    def build_toc_from_structure(self, toc_structure: List[Dict], parent_section=None):
        """Build hierarchical TOC from the parsed structure.

        ebooklib expects nested TOC as tuples: (Section, (children...))
        """
        toc_items = []

        for item in toc_structure:
            if item['type'] == 'link':
                # This is a direct link to a chapter
                chapter = self.chapter_map.get(item['url'])
                if chapter:
                    toc_items.append(chapter)
                else:
                    # Create a link if chapter not found
                    toc_items.append(epub.Link(
                        item['href'], item['title'], item['title'].lower().replace(' ', '_')))
            elif item['type'] == 'section':
                # This is a section with children
                section_title = item['title']
                children = self.build_toc_from_structure(
                    item['children'], section_title)

                if children:
                    # Check if this section has its own chapter (header page)
                    section_chapter = None
                    for chapter in self.chapters:
                        if chapter.title == section_title and chapter.file_name.startswith('section_'):
                            section_chapter = chapter
                            break

                    if section_chapter:
                        # Section has a header page, include it as the first child
                        toc_items.append((section_chapter, tuple(children)))
                    else:
                        # ebooklib expects sections as tuples: (Section, (children...))
                        section = epub.Section(section_title)
                        toc_items.append((section, tuple(children)))
                else:
                    # Empty section, skip it
                    pass

        return toc_items

    def _order_chapters_for_spine(self, toc_structure):
        """Order chapters to match the nav TOC structure for proper spine order."""
        ordered_chapters = []
        chapter_map = {chapter.file_name: chapter for chapter in self.chapters}

        def _extract_chapters_from_toc(toc_items):
            for item in toc_items:
                if isinstance(item, epub.EpubHtml):
                    # This is a chapter
                    if item.file_name in chapter_map:
                        ordered_chapters.append(chapter_map[item.file_name])
                elif isinstance(item, tuple) and len(item) == 2:
                    # This is a section with children: (section, children)
                    section, children = item
                    if hasattr(section, 'file_name') and section.file_name in chapter_map:
                        ordered_chapters.append(chapter_map[section.file_name])
                    # Recursively process children
                    _extract_chapters_from_toc(children)

        _extract_chapters_from_toc(toc_structure)
        return ordered_chapters

    def add_css(self):
        """Add CSS styling."""
        # Base CSS with improved code block styling
        css = '''\n        body {
            font-family: Georgia, serif;
            line-height: 1.6;
            margin: 2em;
        }
        h1, h2, h3, h4, h5, h6 {
            font-family: Arial, sans-serif;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
        }
        h1 { font-size: 2em; }
        h2 { font-size: 1.5em; }
        h3 { font-size: 1.2em; }
        p {
            margin: 1em 0;
        }
        img {
            max-width: 100%;
            height: auto;
            display: block;
            margin: 1em auto;
        }
        code {
            font-family: 'Courier New', 'Consolas', 'Monaco', monospace;
            background-color: #f5f5f5;
            border: 1px solid #ddd;
            border-radius: 3px;
            padding: 2px 6px;
            font-size: 0.9em;
        }
        pre {
            font-family: 'Courier New', 'Consolas', 'Monaco', monospace;
            background-color: #f5f5f5;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 1em;
            overflow-x: auto;
            line-height: 1.4;
            margin: 1em 0;
        }
        pre code {
            background-color: transparent;
            border: none;
            padding: 0;
            font-size: 0.85em;
        }
        blockquote {
            border-left: 4px solid #ccc;
            margin: 1em 0;
            padding-left: 1em;
            font-style: italic;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            margin: 1em 0;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 0.5em;
            text-align: left;
        }
        th {
            background-color: #f5f5f5;
            font-weight: bold;
        }
        '''

        nav_css = epub.EpubItem(
            uid="style_nav",
            file_name="style/nav.css",
            media_type="text/css",
            content=css
        )
        self.book.add_item(nav_css)

        return nav_css

    def add_image(self, filename: str, content: bytes, media_type: str):
        """Add an image to the EPUB book."""
        img = epub.EpubItem(
            uid=filename.replace('/', '_').replace('.', '_'),
            file_name=filename,
            media_type=media_type,
            content=content
        )
        self.book.add_item(img)
        return img

    def add_cover(self, title: str, logo_path: str = "ibm-logo.png"):
        """Generate and add a cover image to the EPUB."""
        import os
        from PIL import Image
        import io

        try:
            # Check if logo exists
            if not os.path.exists(logo_path):
                print(
                    f"Warning: Logo file {logo_path} not found, skipping cover generation")
                return

            # Create HTML for the cover
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{
                        margin: 0;
                        padding: 40px;
                        background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
                        font-family: Arial, sans-serif;
                        display: flex;
                        flex-direction: column;
                        justify-content: center;
                        align-items: center;
                        height: calc(100vh - 80px);
                        text-align: center;
                    }}
                    .logo {{
                        max-width: 200px;
                        max-height: 200px;
                        margin-bottom: 40px;
                    }}
                    .title {{
                        font-size: 36px;
                        font-weight: bold;
                        color: #1f2937;
                        line-height: 1.2;
                        max-width: 600px;
                        margin: 0;
                    }}
                </style>
            </head>
            <body>
                <img src="file://{os.path.abspath(logo_path)}" class="logo" alt="IBM Logo">
                <h1 class="title">{title}</h1>
            </body>
            </html>
            """

            # Convert HTML to PNG using html2image
            hti = Html2Image()
            hti.size = (800, 1200)
            screenshot_path = hti.screenshot(
                html_str=html_content, save_as='cover_temp.png')[0]

            # Read the screenshot
            with open(screenshot_path, 'rb') as f:
                screenshot = f.read()

            # Clean up temp file
            os.unlink(screenshot_path)

            # Convert to PIL Image and resize if needed
            img = Image.open(io.BytesIO(screenshot))

            # Resize to standard cover size (keeping aspect ratio)
            max_width, max_height = 600, 800
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

            # Convert back to bytes
            output = io.BytesIO()
            img.save(output, format='PNG')
            cover_bytes = output.getvalue()

            # Add cover to EPUB
            self.book.set_cover("cover.png", cover_bytes)

            print(f"✓ Generated cover image for: {title}")

        except Exception as e:
            print(f"Warning: Failed to generate cover image: {e}")

    def finalize(self, toc_structure: List[Dict] = None):
        """Finalize the EPUB book structure."""
        # Build hierarchical TOC if structure provided
        if toc_structure:
            self.book.toc = tuple(self.build_toc_from_structure(toc_structure))
        else:
            # Fallback to flat TOC
            self.book.toc = tuple(self.chapters)

        # Add navigation files
        self.book.add_item(epub.EpubNcx())
        self.book.add_item(epub.EpubNav())

        # Add CSS
        css = self.add_css()

        # Add CSS to all chapters
        for chapter in self.chapters:
            chapter.add_item(css)

        # Reorder chapters to match nav TOC order for proper spine
        if toc_structure:
            ordered_chapters = self._order_chapters_for_spine(self.book.toc)
            self.book.spine = ['nav'] + ordered_chapters
        else:
            # Define spine (reading order)
            self.book.spine = ['nav'] + self.chapters

    def write(self, output_path: str):
        """Write the EPUB file."""
        epub.write_epub(output_path, self.book)


def generate_filename_from_title(title: str) -> str:
    """Generate a safe filename from title."""
    # Remove special characters and replace spaces
    safe_name = re.sub(r'[^\w\s-]', '', title)
    safe_name = re.sub(r'[-\s]+', '_', safe_name)
    return safe_name.lower()[:50] + '.xhtml'


@click.command()
@click.argument('url')
@click.option('--output', '-o', default=None, help='Output EPUB filename')
@click.option('--delay', '-d', default=1, type=float, help='Delay between page requests (seconds)')
@click.option('--max-pages', '-m', default=None, type=int, help='Maximum number of pages to convert (for faster iteration)')
def main(url: str, output: Optional[str], delay: float, max_pages: Optional[int]):
    """
    Convert IBM Think guides to EPUB format.

    URL: The URL of the IBM Think guide to convert

    Options:
    --output: Output EPUB filename
    --delay: Delay between page requests (seconds)
    --max-pages: Maximum number of pages to convert (for faster iteration)
    """
    click.echo(f"Fetching guide from: {url}")

    # Initialize scraper
    scraper = IBMThinkScraper(url)

    # Fetch main page
    main_soup = scraper.get_page(url)
    if not main_soup:
        click.echo("Error: Could not fetch the main page", err=True)
        return

    # Extract title for the book
    title_tag = main_soup.find('h1') or main_soup.find('title')
    book_title = title_tag.get_text(
        strip=True) if title_tag else "IBM Think Guide"

    # Generate output filename if not provided
    if not output:
        output = generate_filename_from_title(book_title) + '.epub'
        output = output.replace('.xhtml', '')  # Remove .xhtml if added
        if not output.endswith('.epub'):
            output += '.epub'

    click.echo(f"Book title: {book_title}")
    click.echo(f"Output file: {output}")

    # Extract TOC from sidebar
    click.echo("\nExtracting table of contents from sidebar...")
    toc_structure = scraper.extract_toc_from_sidebar(main_soup)

    if not toc_structure:
        click.echo("No TOC found in sidebar. Adding main page only.")
        toc_structure = [{'title': book_title, 'url': url,
                          'href': '', 'type': 'link', 'level': 0}]

    # Flatten the structure for processing
    toc_items = scraper._flatten_toc_structure(toc_structure)

    if toc_items:
        click.echo(f"Found {len(toc_items)} pages in TOC")
    else:
        click.echo("No pages found in TOC structure")

    # Limit pages if max_pages is specified
    if max_pages is not None and len(toc_items) > max_pages:
        toc_items = toc_items[:max_pages]
        click.echo(f"Limiting to first {max_pages} pages")

        # Create a limited TOC structure that matches the processed pages
        processed_urls = set(item['url'] for item in toc_items)
        toc_structure = scraper._limit_toc_structure(
            toc_structure, processed_urls)

    # Initialize EPUB generator
    epub_gen = EPUBGenerator(book_title)

    # Generate and add cover
    click.echo("Generating cover page...")
    epub_gen.add_cover(book_title)

    # Add header pages for top-level sections that don't have links
    header_chapters = []
    for item in toc_structure:
        if item['type'] == 'section' and item.get('level', 0) == 0:
            # Check if this top-level section has a corresponding page in processed pages
            section_has_page = any(link_item['title'] == item['title']
                                   for link_item in toc_items if link_item['type'] == 'link')

            if not section_has_page:
                # Create a header page for this section
                header_filename = f'section_{len(header_chapters) + 1:03d}.xhtml'

                # Create chapter with empty content first
                header_chapter = epub_gen.add_chapter(
                    item['title'], '', header_filename,
                    f'#{item["title"].lower().replace(" ", "_")}')

                # Override content with centered h1
                header_chapter.content = f'''<div style="display: flex; justify-content: center; align-items: center; height: 100vh; text-align: center;">
    <h1 style="font-size: 2em; margin: 0;">{item["title"]}</h1>
</div>'''
                header_chapters.append(header_chapter)
                click.echo(f"Added section header: {item['title']}")

    # Process each page in the TOC
    for idx, item in enumerate(toc_items, 1):
        page_title = item['title']
        page_url = item['url']

        click.echo(f"\nProcessing [{idx}/{len(toc_items)}]: {page_title}")

        # Fetch the page
        page_soup = scraper.get_page(page_url)
        if not page_soup:
            click.echo(f"  Skipping (fetch failed)")
            continue

        # Extract content
        content = scraper.extract_content(page_soup)
        if not content or len(content.strip()) < 100:
            click.echo(f"  Skipping (no content found)")
            continue

        # Process images and clean links in content
        content_soup = BeautifulSoup(content, 'lxml')
        content_soup = scraper.process_images(content_soup)
        content_soup = scraper.clean_links(content_soup)
        content = str(content_soup)

        # Clean content
        content = scraper.clean_html(content)

        # Generate filename for chapter
        filename = f"chapter_{idx:03d}.xhtml"

        # Add chapter to EPUB
        epub_gen.add_chapter(page_title, content, filename, page_url)
        click.echo(f"  Added to EPUB ({len(content)} bytes)")

        # Respectful delay between requests
        if idx < len(toc_items):
            time.sleep(delay)

    # Add all downloaded images to EPUB
    click.echo(f"\nAdding {len(scraper.downloaded_images)} images to EPUB...")
    for img_url, img_data in scraper.downloaded_images.items():
        epub_gen.add_image(
            img_data['filename'],
            img_data['content'],
            img_data['media_type']
        )

    # Rewrite internal links to point within the EPUB
    click.echo("Rewriting internal links...")
    for chapter in epub_gen.chapters:
        if hasattr(chapter, 'content') and chapter.content:
            content_soup = BeautifulSoup(chapter.content, 'lxml')
            content_soup = scraper.clean_links(
                content_soup, epub_gen.chapter_map)
            chapter.content = str(content_soup)

    # Finalize and write EPUB
    click.echo("Finalizing EPUB...")
    epub_gen.finalize(toc_structure)
    epub_gen.write(output)

    click.echo(f"\n✓ Successfully created: {output}")
    click.echo(f"  Chapters: {len(epub_gen.chapters)}")
    click.echo(f"  Images: {len(scraper.downloaded_images)}")


if __name__ == '__main__':
    main()
