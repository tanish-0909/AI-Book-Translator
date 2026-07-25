import os
import json
from nltk.corpus import wordnet as wn

def append_wordnet(output_jsonl):
    """
    Appends Marathi WordNet synsets to the unified JSONL file.
    """
    if not os.path.exists(output_jsonl):
        print(f"Error: {output_jsonl} does not exist. Cannot append.")
        return
        
    print("Appending Marathi WordNet to unified corpus...")
    
    with open(output_jsonl, 'a', encoding='utf-8') as f:
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
                
        print(f"Successfully appended {count} Marathi WordNet synsets.")

if __name__ == "__main__":
    output_file = "./dictionary/data/unified_marathi_corpus.jsonl"
    append_wordnet(output_file)
