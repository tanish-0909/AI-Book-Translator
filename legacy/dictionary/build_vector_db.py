import json
import chromadb
from chromadb.utils import embedding_functions

def build_chroma_db(jsonl_path: str, persist_directory: str = "../dictionary/chroma_db", batch_size: int = 5461):
    """
    Ingests the 1.1GB unified_marathi_corpus.jsonl into a local ChromaDB.
    Note: For a 3.6M entry corpus, this will take significant time.
    """
    print("Initializing ChromaDB Client...")
    client = chromadb.PersistentClient(path=persist_directory)
    
    # Use a lightweight multilingual embedding model for fast ingestion
    sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="paraphrase-multilingual-MiniLM-L12-v2"
    )
    
    collection = client.get_or_create_collection(
        name="marathi_dictionary", 
        embedding_function=sentence_transformer_ef
    )
    
    print(f"Reading from {jsonl_path}...")
    documents = []
    metadatas = []
    ids = []
    
    count = 0
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            data = json.loads(line)
            eng_text = data.get("english", "")
            mar_text = data.get("marathi", "")
            source = data.get("source", "unknown")
            
            # Index by the English text for retrieval during translation
            documents.append(eng_text)
            metadatas.append({"marathi": mar_text, "source": source})
            ids.append(f"entry_{count}")
            
            count += 1
            
            if len(documents) >= batch_size:
                print(f"Ingesting batch up to {count}...")
                collection.add(
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids
                )
                documents, metadatas, ids = [], [], []
                
    if documents:
        collection.add(documents=documents, metadatas=metadatas, ids=ids)
        
    print(f"Successfully ingested {count} entries into ChromaDB at {persist_directory}.")

if __name__ == "__main__":
    # To run this, ensure `pip install chromadb sentence-transformers` is installed.
    # build_chroma_db("data/unified_marathi_corpus.jsonl")
    print("ChromaDB vector ingestion script ready.")
