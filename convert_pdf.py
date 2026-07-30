import zipfile
import html
import sys
import os
import re
from pypdf import PdfReader

def create_epub(pdf_path, epub_path, book_title):
    extracted_lines = []
    try:
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            text = page.extract_text()
            if text:
                extracted_lines.extend(text.split('\n'))
    except Exception as e:
        print(f"Error reading PDF: {e}")
        sys.exit(1)

    if not extracted_lines:
        print("No text extracted. The PDF might be empty or image-based.")
        sys.exit(1)

    html_blocks = []
    current_paragraph = []
    headings_for_toc = []

    def flush_paragraph():
        if current_paragraph:
            text = " ".join(current_paragraph).strip()
            text = re.sub(r'\s+', ' ', text)
            if text:
                html_blocks.append(f"<p>{html.escape(text)}</p>")
            current_paragraph.clear()

    for line in extracted_lines:
        has_indent = line.startswith(' ') or line.startswith('\t')
        clean_line = line.strip()
        
        if not clean_line:
            flush_paragraph()
            continue
            
        if clean_line.isdigit() and len(clean_line) < 5:
            continue
            
        is_caps_heading = clean_line.isupper() and any(c.isalpha() for c in clean_line) and len(clean_line) < 100
        is_subtitle = len(clean_line) < 60 and not clean_line.endswith(('.', '!', '?', ',', ':', ';')) and not current_paragraph
        
        if is_caps_heading:
            flush_paragraph()
            heading_id = f"heading_{len(headings_for_toc) + 1}"
            headings_for_toc.append((heading_id, clean_line))
            html_blocks.append(f'<h2 id="{heading_id}">{html.escape(clean_line)}</h2>')
        
        elif is_subtitle:
            flush_paragraph()
            html_blocks.append(f'<h3>{html.escape(clean_line)}</h3>')
        
        else:
            if has_indent and current_paragraph:
                flush_paragraph()
                
            current_paragraph.append(clean_line)
            
            # Universal punctuation break check (includes standard and Cyrillic guillemets)
            if len(clean_line) < 75 and clean_line[-1] in '.!?:;"»\'”’':
                flush_paragraph()
                
    flush_paragraph()

    safe_title = html.escape(book_title)

    html_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>{safe_title}</title>
  <style>
    body {{ font-family: serif; padding: 2%; }}
    h2 {{ text-align: center; margin-top: 2em; margin-bottom: 1em; font-size: 1.4em; page-break-before: always; }}
    h3 {{ text-align: center; margin-top: 1em; margin-bottom: 1em; font-weight: normal; font-style: italic; }}
    p {{ text-align: justify; text-indent: 1.5em; margin-top: 0.3em; margin-bottom: 0.3em; line-height: 1.4; }}
  </style>
</head>
<body>
  {''.join(html_blocks)}
</body>
</html>"""

    nav_points = ""
    for idx, (h_id, h_text) in enumerate(headings_for_toc, 1):
        nav_points += f'''
    <navPoint id="navPoint-{idx}" playOrder="{idx}">
      <navLabel>
        <text>{html.escape(h_text)}</text>
      </navLabel>
      <content src="chapter1.html#{h_id}"/>
    </navPoint>'''

    with zipfile.ZipFile(epub_path, 'w') as epub:
        epub.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        
        epub.writestr('META-INF/container.xml', '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>''')
        
        epub.writestr('OEBPS/content.opf', f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookID" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{safe_title}</dc:title>
    <dc:language>ru</dc:language>
    <dc:identifier id="BookID">urn:uuid:12345</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chapter1" href="chapter1.html" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter1"/>
  </spine>
</package>''')
        
        epub.writestr('OEBPS/toc.ncx', f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:12345"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>{safe_title}</text>
  </docTitle>
  <navMap>
    {nav_points}
  </navMap>
</ncx>''')
        
    print(f"Success! Structural EPUB generated at: {epub_path}")

if __name__ == "__main__":
    # Check if a file argument was passed
    if len(sys.argv) < 2:
        print("Usage: python3 convert_pdf.py <input_file.pdf>")
        sys.exit(1)
        
    input_pdf = sys.argv[1]
    
    # Ensure the file actually exists
    if not os.path.isfile(input_pdf):
        print(f"Error: The file '{input_pdf}' does not exist.")
        sys.exit(1)
        
    # Extract base name without the .pdf extension
    base_name = os.path.splitext(os.path.basename(input_pdf))[0]
    output_epub = f"{base_name}.epub"
    
    # Collision handling: Append -1, -2, etc. if the file already exists
    counter = 1
    while os.path.exists(output_epub):
        output_epub = f"{base_name}-{counter}.epub"
        counter += 1
        
    # Extract the title from PDF metadata for the EPUB table of contents
    try:
        reader = PdfReader(input_pdf)
        book_title = reader.metadata.title if reader.metadata and reader.metadata.title else base_name
    except:
        book_title = base_name
        
    create_epub(input_pdf, output_epub, book_title)
