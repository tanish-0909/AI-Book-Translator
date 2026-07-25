import os
import json
from datasets import load_from_disk
import nltk
from nltk.corpus import wordnet as wn

def create_unified_dataset(samanantar_path, flores_path, output_jsonl):
    """
    Combines datasets into a single JSONL file structured for LLMs and RAG.
    Format:
    {
      "english": "...",
      "marathi": "...",
      "source": "samanantar|flores200|wordnet",
      "type": "parallel_sentence|dictionary_synset"
    }
    """
    os.makedirs(os.path.dirname(output_jsonl), exist_ok=True)
    
    with open(output_jsonl, 'w', encoding='utf-8') as f:
        
        # 1. FLORES-200
        print("Processing FLORES-200...")
        if os.path.exists(flores_path):
            flores_ds = load_from_disk(flores_path)
            for item in flores_ds:
                record = {
                    "english": item.get('sentence_eng_Latn', ''),
                    "marathi": item.get('sentence_mar_Deva', ''),
                    "source": "flores-200",
                    "type": "parallel_sentence"
                }
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
            print(f"Added {len(flores_ds)} FLORES-200 records.")
        else:
            print(f"FLORES-200 not found at {flores_path}")

        # 2. Samanantar (Iterate to save memory)
        print("Processing AI4Bharat Samanantar (this may take a minute)...")
        if os.path.exists(samanantar_path):
            samanantar_ds = load_from_disk(samanantar_path)
            # Samanantar schema: 'src' (english) and 'tgt' (marathi)
            count = 0
            for item in samanantar_ds:
                record = {
                    "english": item.get('src', ''),
                    "marathi": item.get('tgt', ''),
                    "source": "samanantar",
                    "type": "parallel_sentence"
                }
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
                count += 1
            print(f"Added {count} Samanantar records.")
        else:
            print(f"Samanantar not found at {samanantar_path}")

        # 3. Marathi WordNet (via NLTK OMW)
        print("Processing Marathi WordNet...")
        try:
            nltk.data.path.append("./data")
            count = 0
            for synset in wn.all_synsets():
                marathi_lemmas = synset.lemma_names('mar')
                english_lemmas = synset.lemma_names('eng')
                
                if marathi_lemmas:
                    record = {
                        "english": english_lemmas,
                        "marathi": marathi_lemmas,
                        "definition": synset.definition(),
                        "source": "omw_wordnet",
                        "type": "dictionary_synset"
                    }
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')
                    count += 1
            print(f"Added {count} Marathi WordNet synsets.")
        except Exception as e:
            print(f"WordNet processing failed: {e}")

    print(f"\nSuccessfully combined all resources into: {output_jsonl}")

if __name__ == "__main__":
    samanantar_dir = "./data/samanantar_mr"
    flores_dir = "./data/flores_200_mr"
    output_file = "./dictionary/data/unified_marathi_corpus.jsonl"
    
    create_unified_dataset(samanantar_dir, flores_dir, output_file)
