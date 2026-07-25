import json

def cross_validate(pymupdf_elements, ocr_elements, mineru_elements):
    """
    Compares the extraction results from PyMuPDF, OCR (pdf-craft), and MinerU.
    Returns True if they match sufficiently, False if there is a discrepancy.
    """
    def get_char_count(elements):
        return sum(len(el.get("text", "")) for el in elements if el.get("type") != "image_ref")
        
    py_count = get_char_count(pymupdf_elements)
    ocr_count = get_char_count(ocr_elements)
    mineru_count = get_char_count(mineru_elements)
    
    counts = [py_count, ocr_count, mineru_count]
    
    # If the page is mostly blank, skip complex validation
    if max(counts) < 20:
        return True, "Match (Low Text Volume)"
    
    # Check for complete failure of one method
    if min(counts) < (max(counts) * 0.5):
        return False, f"Major Discrepancy: PyMuPDF={py_count}, OCR={ocr_count}, MinerU={mineru_count}"
        
    # Check 10% variance
    avg = sum(counts) / 3
    for c in counts:
        if abs(c - avg) / (avg + 1) > 0.10:
            return False, f"Discrepancy > 10%: PyMuPDF={py_count}, OCR={ocr_count}, MinerU={mineru_count}"
            
    # Check for garbage (cid:N) in PyMuPDF
    for el in pymupdf_elements:
        if "(cid:" in el.get("text", ""):
            return False, "Garbage (cid:N) characters detected in PyMuPDF extraction."
            
    return True, "Match"

if __name__ == "__main__":
    print("Diagnostics module ready.")
