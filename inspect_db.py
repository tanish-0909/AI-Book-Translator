import sqlite3

conn = sqlite3.connect("workdir/state.db")
c = conn.cursor()
c.execute("SELECT seq_order, source_text, draft_translation, final_translation, reflection_notes FROM paragraphs ORDER BY seq_order LIMIT 15")
rows = c.fetchall()

with open("db_dump.txt", "w", encoding="utf-8") as f:
    for row in rows:
        f.write(f"--- Paragraph {row[0]} ---\n")
        f.write(f"SOURCE: {row[1]}\n")
        f.write(f"DRAFT: {row[2]}\n")
        f.write(f"FINAL: {row[3]}\n")
        f.write(f"NOTES: {row[4]}\n\n")
