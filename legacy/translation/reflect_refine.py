import json
import requests
import re
from .context_manager import ContextManager

OLLAMA_URL = "http://localhost:11434/api/generate"

def refine_paragraph(source_text: str, draft_text: str, context_mgr: ContextManager, model="qwen2.5:7b-instruct-q4_K_M"):
    """
    Calls Ollama to reflect on and refine the draft translation.
    Returns the JSON payload containing notes, final translation, and new terms.
    """
    prompt = context_mgr.build_reflect_prompt(source_text, draft_text)
    
    # Section 4.10: Idiom Overpass is implicitly handled in the context manager prompt
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json"
    }
    
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        
        result_text = response.json().get("response", "{}")
        
        # Parse the JSON response
        try:
            parsed = json.loads(result_text)
            return parsed
        except json.JSONDecodeError:
            # Fallback if the LLM didn't return perfect JSON
            match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            return {"final_translation": draft_text, "notes": "JSON decode failed, fallback to draft."}
            
    except Exception as e:
        print(f"Error calling Ollama: {e}")
        return {"final_translation": draft_text, "notes": f"Error: {str(e)}"}

if __name__ == "__main__":
    print("Reflect and Refine module ready.")
