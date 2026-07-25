import os
import sqlite3
import yaml
import logging
from extraction.extract_text import extract_raw_text
from extraction.extract_images import extract_images
from translation.draft import load_draft_model, draft_translate_batch
from translation.context_manager import ContextManager
from translation.reflect_refine import refine_paragraph
from reassembly.build_docx import export_marathi_docx

def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS paragraphs (
            id TEXT PRIMARY KEY,
            page INTEGER NOT NULL,
            chapter_id TEXT,
            seq_order INTEGER NOT NULL,
            element_type TEXT NOT NULL,
            source_text TEXT,
            image_ref TEXT,
            bbox TEXT,
            extraction_method TEXT,
            extraction_confidence REAL,
            draft_translation TEXT,
            reflection_notes TEXT,
            final_translation TEXT,
            new_terms_json TEXT,
            status TEXT DEFAULT 'pending',
            flagged_reason TEXT,
            updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_status ON paragraphs(status);
        CREATE INDEX IF NOT EXISTS idx_seq ON paragraphs(seq_order);
        
        CREATE TABLE IF NOT EXISTS run_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP, 
            stage TEXT, 
            message TEXT, 
            level TEXT
        );
    """)
    conn.commit()
    return conn

def log_event(conn, stage, message, level="INFO"):
    c = conn.cursor()
    c.execute("INSERT INTO run_log (stage, message, level) VALUES (?, ?, ?)", 
              (stage, message, level))
    conn.commit()
    logging.info(f"[{stage}] {message}")

def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    config = load_config()
    db_path = config["paths"]["state_db"]
    conn = init_db(db_path)
    
    log_event(conn, "ORCHESTRATOR", "Pipeline started.")
    
    pdf_path = config["paths"]["input_pdf"]
    if not os.path.exists(pdf_path):
        alt_path = "./input/Book.pdf"
        if os.path.exists(alt_path):
            pdf_path = alt_path
        else:
            log_event(conn, "ERROR", f"File not found: {pdf_path}")
            return
            
    # STAGE 1: EXTRACTION
    log_event(conn, "ORCHESTRATOR", "Starting Stage 1: Extraction")
    images = extract_images(pdf_path, config["paths"]["images_dir"])
    elements = extract_raw_text(pdf_path)
    
    c = conn.cursor()
    for i, el in enumerate(elements):
        p_id = f"p_{el['page']}_{i}"
        c.execute("""
            INSERT OR IGNORE INTO paragraphs 
            (id, page, seq_order, element_type, source_text, bbox, extraction_method)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (p_id, el['page'], i, el.get('type', 'paragraph'), el['text'], str(el['bbox']), el['extraction_method']))
    conn.commit()
    
    # STAGE 2: TRANSLATION
    log_event(conn, "ORCHESTRATOR", "Starting Stage 2: Translation")
    c.execute("SELECT id, source_text, element_type FROM paragraphs WHERE status='pending' ORDER BY seq_order ASC")
    pending_rows = c.fetchall()
    
    if pending_rows:
        pending = [{"id": row[0], "text": row[1], "type": row[2]} for row in pending_rows]
        
        # We dummy-init the context manager since files might not exist yet
        os.makedirs("./context", exist_ok=True)
        with open("./context/glossary.json", "w") as f:
            f.write("{}")
        with open("./context/summary.md", "w") as f:
            f.write("Beginning of the book.")
            
        context_mgr = ContextManager(
            glossary_path="./context/glossary.json",
            summary_path="./context/summary.md",
            chroma_db_path="./dictionary/chroma_db"
        )
        
        try:
            tokenizer, model, ip = load_draft_model()
            drafted_paragraphs = draft_translate_batch(pending, tokenizer, model, ip, batch_size=8)
            
            # Free VRAM
            del model
            del tokenizer
            import torch
            torch.cuda.empty_cache()
            
            log_event(conn, "STAGE_2", "Drafting complete. Running Ollama Reflect step...")
            for p in drafted_paragraphs:
                if p["status"] == "drafted" and p.get("type") not in ["code_block", "table", "equation", "image_ref"]:
                    refined = refine_paragraph(p["text"], p["draft_translation"], context_mgr)
                    final_text = refined.get("final_translation", p["draft_translation"])
                    notes = refined.get("notes", "")
                else:
                    final_text = p.get("draft_translation", p["text"])
                    notes = "Bypassed"
                    
                import json
                if not isinstance(final_text, str):
                    final_text = json.dumps(final_text, ensure_ascii=False)
                if not isinstance(notes, str):
                    notes = json.dumps(notes, ensure_ascii=False)
                    
                c.execute("""
                    UPDATE paragraphs 
                    SET draft_translation=?, final_translation=?, reflection_notes=?, status='final' 
                    WHERE id=?
                """, (p.get("draft_translation"), final_text, notes, p["id"]))
            conn.commit()
        except Exception as e:
            log_event(conn, "ERROR", f"Translation failed: {e}")
            
    # STAGE 4: REASSEMBLY
    log_event(conn, "ORCHESTRATOR", "Starting Stage 4: Reassembly")
    os.makedirs(config["paths"]["output_dir"], exist_ok=True)
    out_docx = os.path.join(config["paths"]["output_dir"], "book_marathi.docx")
    export_marathi_docx(db_path, out_docx, font_name=config["output"]["font"])
    
    log_event(conn, "ORCHESTRATOR", f"Pipeline completed! Saved to {out_docx}")
    print(f"\nSUCCESS! Translated book saved to {out_docx}")
    conn.close()

if __name__ == "__main__":
    run()
