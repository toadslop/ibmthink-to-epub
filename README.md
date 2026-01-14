# IBM Think to EPUB Converter

A CLI tool to convert IBM Think guides into valid, well-formatted EPUB files.

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python ibm_think_to_epub.py <url> [--output <filename.epub>] [--delay <seconds>] [--max-pages <number>]
```

### Examples

```bash
# Convert a machine learning guide
python ibm_think_to_epub.py https://www.ibm.com/think/machine-learning

# Convert with custom output filename
python ibm_think_to_epub.py https://www.ibm.com/think/ai-agents --output ai-agents-guide.epub

# Adjust delay between page requests (default: 1 second)
python ibm_think_to_epub.py https://www.ibm.com/think/machine-learning --delay 0.5

# Convert only the first 3 pages for faster testing
python ibm_think_to_epub.py https://www.ibm.com/think/machine-learning --max-pages 3
```

## Features

- ✅ Automatically extracts TOC from sidebar navigation (`side-nav-section`)
- ✅ Iterates through all pages in the guide
- ✅ Extracts only main content from `body-article-8` divs
- ✅ Removes extraneous content (ads, tracking, share modules, etc.)
- ✅ Downloads and embeds all images
- ✅ Clean, EPUB-compliant CSS with proper code block formatting
- ✅ Passes epubcheck validation with no errors
- ✅ Generates well-formatted EPUB files compatible with all readers

## Content Filtering

The tool specifically removes:
- `article-content-slot` elements (ads/promotions)
- `share-module` elements (social sharing)
- `author-signature` elements
- Scripts, styles, navigation, footers
- External and broken links

## Validation

All generated EPUB files pass epubcheck validation:
```bash
epubcheck output.epub
# No errors or warnings detected
```
