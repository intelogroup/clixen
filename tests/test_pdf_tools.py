import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools-harness'))

def test_pdf_to_markdown_returns_string(tmp_path):
    import fitz
    from tools.pdf_tools import pdf_to_markdown

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello from test PDF")
    pdf_path = tmp_path / "sample.pdf"
    doc.save(str(pdf_path))
    doc.close()

    md_path, md_content = pdf_to_markdown(str(pdf_path), str(tmp_path))
    assert md_content.strip() != ""
    assert md_path.endswith(".md")
    assert os.path.exists(md_path)

def test_extract_pdf_images_empty_pdf(tmp_path):
    import fitz
    from tools.pdf_tools import extract_pdf_images

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "No images here")
    pdf_path = tmp_path / "noimg.pdf"
    doc.save(str(pdf_path))
    doc.close()

    images = extract_pdf_images(str(pdf_path), str(tmp_path))
    assert images == []
