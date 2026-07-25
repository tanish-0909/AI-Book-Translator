import fitz

def extract_raw_text(pdf_path: str) -> list[dict]:
    """
    Extracts raw text and bounding boxes from a PDF using PyMuPDF (Path A).
    Returns a list of paragraph/element dictionaries.
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"Error opening PDF: {e}")
        return []

    elements = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("dict").get("blocks", [])
        
        for block in blocks:
            if block.get("type") == 0:  # 0 denotes text block
                bbox = block["bbox"]
                text_content = ""
                
                # Reconstruct text block preserving simple spaces
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text_content += span["text"] + " "
                
                text_content = text_content.strip()
                if text_content:
                    elements.append({
                        "page": page_num + 1,
                        "bbox": bbox,
                        "text": text_content,
                        "extraction_method": "text_layer"
                    })
                    
    doc.close()
    return elements

if __name__ == "__main__":
    print("PyMuPDF Extractor module ready.")
