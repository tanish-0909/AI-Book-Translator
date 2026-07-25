import json
import os
import chromadb
from chromadb.utils import embedding_functions

class ContextManager:
    def __init__(self, glossary_path: str, summary_path: str, chroma_db_path: str):
        self.glossary_path = glossary_path
        self.summary_path = summary_path
        
        # Init ChromaDB Client for RAG
        self.chroma_client = chromadb.PersistentClient(path=chroma_db_path)
        self.ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
        self.collection = self.chroma_client.get_or_create_collection(
            name="marathi_dictionary", 
            embedding_function=self.ef
        )
        
    def get_glossary(self):
        if not os.path.exists(self.glossary_path):
            return {}
        with open(self.glossary_path, "r", encoding="utf-8") as f:
            return json.load(f)
            
    def get_summary(self):
        if not os.path.exists(self.summary_path):
            return "No summary available yet."
        with open(self.summary_path, "r", encoding="utf-8") as f:
            return f.read()
            
    def query_rag_dictionary(self, query_text: str, n_results: int = 3):
        """
        Performs a semantic search on the unified dictionary to find 
        parallel examples or official terminology for the given text.
        """
        results = self.collection.query(
            query_texts=[query_text],
            n_results=n_results
        )
        
        matches = []
        if results and "documents" in results and results["documents"]:
            for idx, eng_text in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][idx]
                matches.append(f"English: {eng_text} | Marathi: {meta['marathi']} (Source: {meta['source']})")
                
        return "\n".join(matches)

    def build_reflect_prompt(self, source_text: str, draft_text: str):
        glossary = self.get_glossary()
        summary = self.get_summary()
        rag_matches = self.query_rag_dictionary(source_text)
        
        prompt = f"""You are a professional Marathi literary translator. 
Analyze this draft translation.

STORY SO FAR:
{summary}

RELEVANT GLOSSARY ENTRIES:
{json.dumps(glossary, ensure_ascii=False)}

RELEVANT DICTIONARY / PARALLEL EXAMPLES (RAG):
{rag_matches}

SOURCE ENGLISH:
{source_text}

DRAFT MARATHI:
{draft_text}

Check for tone, continuity, grammatical gender, and idioms. 
Output your analysis as JSON."""
        return prompt

if __name__ == "__main__":
    print("Context Manager module ready.")
