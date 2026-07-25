import fitz
import os

def extract_images(pdf_path: str, output_dir: str) -> list[dict]:
    """
    Extracts embedded images at original resolution using PyMuPDF.
    Returns a manifest of images.
    """
    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    manifest = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        for img_index, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            base_image = doc.extract_image(xref)  
            if not base_image:
                continue
                
            ext = base_image["ext"]
            filename = f"page{page_num+1:04d}_img{img_index+1:02d}.{ext}"
            filepath = os.path.join(output_dir, filename)
            
            with open(filepath, "wb") as f:
                f.write(base_image["image"])
                
            manifest.append({
                "page": page_num + 1,
                "filename": filename,
                "xref": xref,
            })
            
    doc.close()
    return manifest

if __name__ == "__main__":
    # Test block
    print("Image extractor initialized.")
