import os
import pdfplumber
import pandas as pd
import argparse
import re

def parse_maharashtra_pdfs(pdf_dir, output_csv):
    """
    Parses administrative dictionary PDFs (like Shasan Vyavahar Kosh)
    into a structured CSV file.
    """
    os.makedirs(pdf_dir, exist_ok=True)
    pdf_files = [f for f in os.listdir(pdf_dir) if f.lower().endswith('.pdf')]
    
    if not pdf_files:
        print(f"No PDFs found in {pdf_dir}. Please place the Maharashtra Government Dictionary PDFs there.")
        return
    
    print(f"Found {len(pdf_files)} PDF(s). Starting extraction...")
    
    dictionary_data = []
    
    for pdf_file in pdf_files:
        pdf_path = os.path.join(pdf_dir, pdf_file)
        print(f"Processing {pdf_file}...")
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    # Most dictionaries have tabular formats or aligned columns
                    # We will try to extract table lines
                    text = page.extract_text()
                    if not text:
                        continue
                        
                    lines = text.split('\n')
                    for line in lines:
                        # Very basic heuristic: English word usually starts with ASCII, 
                        # followed by spaces, followed by Marathi (Devanagari)
                        # Example: "Administration   प्रशासन"
                        
                        # Match english part (mostly a-z, spaces, hyphens) and then non-english (marathi)
                        match = re.match(r'^([A-Za-z0-9\s\(\)\-\.,]+?)\s{2,}(.+)$', line)
                        if match:
                            english_word = match.group(1).strip()
                            marathi_word = match.group(2).strip()
                            
                            if english_word and marathi_word:
                                dictionary_data.append({
                                    "English": english_word,
                                    "Marathi": marathi_word,
                                    "Source": pdf_file,
                                    "Page": page_num + 1
                                })
        except Exception as e:
            print(f"Error processing {pdf_file}: {e}")

    if dictionary_data:
        df = pd.DataFrame(dictionary_data)
        # Drop likely false positives (e.g. headers, single letters)
        df = df[df["English"].str.len() > 1]
        
        os.makedirs(os.path.dirname(output_csv), exist_ok=True)
        df.to_csv(output_csv, index=False, encoding='utf-8')
        print(f"\nExtraction complete! Extracted {len(df)} entries.")
        print(f"Saved to: {output_csv}")
    else:
        print("\nCould not extract any standard dictionary pairs.")
        print("The PDF might not have a standard 'English   Marathi' column layout.")
        print("You may need a custom OCR script depending on the exact PDF formatting.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse Maharashtra Government Dictionary PDFs.")
    parser.add_argument("--pdf_dir", type=str, default="./pdfs", help="Directory containing the PDFs")
    parser.add_argument("--output_csv", type=str, default="./data/maharashtra_gov_dict.csv", help="Output CSV path")
    
    args = parser.parse_args()
    parse_maharashtra_pdfs(args.pdf_dir, args.output_csv)
