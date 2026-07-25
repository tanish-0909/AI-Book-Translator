import os
import argparse
from datasets import load_dataset
import nltk

def download_open_source_datasets(output_dir):
    """
    Downloads the openly available datasets mentioned in the resources list and saves them to disk.
    """
    os.makedirs(output_dir, exist_ok=True)
    print(f"--- Downloading Datasets to {output_dir} ---")
    
    # 1. L3Cube MahaCorpus (Monolingual Marathi)
    print("\n[1/4] Downloading L3Cube MahaCorpus...")
    try:
        maha_corpus = load_dataset("l3cube-pune/marathi-corpus", split="train")
        maha_corpus_path = os.path.join(output_dir, "l3cube_mahacorpus")
        maha_corpus.save_to_disk(maha_corpus_path)
        print(f"Successfully saved L3Cube MahaCorpus to {maha_corpus_path}")
    except Exception as e:
        print(f"Failed to load L3Cube MahaCorpus: {e}")

    # 2. Samanantar (Parallel Marathi-English)
    print("\n[2/4] Downloading AI4Bharat Samanantar (Marathi)...")
    try:
        samanantar = load_dataset("ai4bharat/samanantar", "mr", split="train")
        samanantar_path = os.path.join(output_dir, "samanantar_mr")
        samanantar.save_to_disk(samanantar_path)
        print(f"Successfully saved Samanantar to {samanantar_path}")
    except Exception as e:
        print(f"Failed to load Samanantar: {e}")

    # 3. FLORES-200 (Evaluation Parallel Data)
    print("\n[3/4] Downloading FLORES-200 (Marathi)...")
    try:
        flores = load_dataset("facebook/flores", "mar_Deva-eng_Latn", split="dev")
        flores_path = os.path.join(output_dir, "flores_200_mr")
        flores.save_to_disk(flores_path)
        print(f"Successfully saved FLORES-200 to {flores_path}")
    except Exception as e:
        print(f"Failed to load FLORES-200: {e}")

    # 4. Marathi WordNet (via Open Multilingual WordNet)
    print("\n[4/4] Downloading Open Multilingual WordNet (includes Marathi)...")
    try:
        nltk.download('omw-1.4', download_dir=output_dir)
        nltk.download('wordnet', download_dir=output_dir)
        print("Successfully downloaded WordNet data.")
    except Exception as e:
        print(f"Failed to download WordNet data: {e}")
        
    print("\n--- Download Complete ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download open-source Marathi datasets.")
    parser.add_argument("--output_dir", type=str, default="./data", help="Directory to save the datasets")
    args = parser.parse_args()
    
    download_open_source_datasets(args.output_dir)
