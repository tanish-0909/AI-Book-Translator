import re
import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
from IndicTransToolkit.processor import IndicProcessor

MODEL_NAME = "ai4bharat/indictrans2-en-indic-1B"

def is_code_or_syntax(text: str) -> bool:
    """
    Section 4.10 Final Polish (Regex Code Bypass).
    Checks if a string is likely a code snippet or programming syntax.
    """
    # Match markdown code blocks, camelCase, JSON, or curly braces
    code_patterns = [
        r"```",               # Markdown code blocks
        r"[{}]",              # Curly braces (JSON, C-style code)
        r"\b[a-z]+[A-Z][a-z]+\b", # camelCase variable names
        r"^\s*<[^>]+>\s*$"    # HTML tags
    ]
    for pattern in code_patterns:
        if re.search(pattern, text):
            return True
    return False

def load_draft_model():
    print("Loading IndicTrans2 model...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using compute device: {device}")
    
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(
        MODEL_NAME, 
        trust_remote_code=True, 
        torch_dtype=torch.float16 if device == "cuda" else torch.float32
    ).to(device)
    
    ip = IndicProcessor(inference=True)
    return tokenizer, model, ip

def draft_translate_batch(paragraphs: list[dict], tokenizer, model, ip,
                          src_lang="eng_Latn", tgt_lang="mar_Deva", batch_size=16) -> list[dict]:
    """
    Batched draft translation using IndicTrans2.
    paragraphs: list of dicts {"id": "...", "text": "...", "type": "..."}
    """
    results = []
    
    # Pre-filter code blocks (Section 4.10)
    to_translate = []
    to_translate_indices = []
    
    for idx, p in enumerate(paragraphs):
        p_type = p.get("type")
        if p_type in ["image_ref", "table", "equation"] or is_code_or_syntax(p["text"]):
            # Bypass translation for images, code, tables, and equations
            p["draft_translation"] = p["text"]
            p["status"] = "drafted"
            if is_code_or_syntax(p["text"]) and p_type not in ["table", "equation"]:
                p["type"] = "code_block"
            results.append(p)
        else:
            to_translate.append(p["text"])
            to_translate_indices.append(idx)
            results.append(p) # Placeholder, will update draft_translation below
            
    if not to_translate:
        return results
        
    print(f"Drafting {len(to_translate)} paragraphs in batches of {batch_size}...")
    
    for i in range(0, len(to_translate), batch_size):
        batch = to_translate[i:i+batch_size]
        prepped = ip.preprocess_batch(batch, src_lang=src_lang, tgt_lang=tgt_lang)
        
        inputs = tokenizer(prepped, padding=True, truncation=True,
                           max_length=1024, return_tensors="pt").to(model.device)
                           
        with torch.no_grad():
            generated = model.generate(**inputs, max_length=1024, num_beams=5)
            
        decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)
        postprocessed = ip.postprocess_batch(decoded, lang=tgt_lang)
        
        # Map back to results
        for j, translated_text in enumerate(postprocessed):
            original_idx = to_translate_indices[i + j]
            results[original_idx]["draft_translation"] = translated_text
            results[original_idx]["status"] = "drafted"
            
    return results

if __name__ == "__main__":
    print("Draft translation module ready.")
