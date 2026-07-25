import sqlite3
import json
import os

def check_glossary_consistency(db_path: str, glossary_path: str):
    """
    Scans the final translations in the DB to ensure glossary terms were used correctly.
    Flags paragraphs in the SQLite database if an inconsistency is found.
    """
    if not os.path.exists(glossary_path):
        print("No glossary found, skipping check.")
        return []
        
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    with open(glossary_path, "r", encoding="utf-8") as f:
        glossary = json.load(f)
        
    c.execute("SELECT id, source_text, final_translation FROM paragraphs WHERE status='final'")
    rows = c.fetchall()
    
    inconsistencies = []
    
    for row in rows:
        p_id, source, final = row
        
        # Skip if bypassed/empty
        if not final or not source:
            continue
            
        for eng_term, mar_term in glossary.items():
            # If the English term is in the source, the Marathi term must be in the final
            if eng_term.lower() in source.lower():
                if mar_term not in final:
                    inconsistencies.append({
                        "id": p_id,
                        "term": eng_term,
                        "expected": mar_term
                    })
                    
                    # Update status in DB to flagged
                    c.execute("UPDATE paragraphs SET status='flagged', flagged_reason=? WHERE id=?", 
                              (f"Glossary mismatch: {eng_term} -> {mar_term}", p_id))
                              
    conn.commit()
    conn.close()
    
    print(f"Glossary consistency check complete. Found {len(inconsistencies)} issues.")
    return inconsistencies

if __name__ == "__main__":
    print("QA Glossary consistency module ready.")
