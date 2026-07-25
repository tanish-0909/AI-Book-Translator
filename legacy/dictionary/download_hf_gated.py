import os
import argparse
from datasets import load_dataset
from huggingface_hub import login

def download_gated_datasets(output_dir, hf_token):
    """
    Downloads gated datasets (like FLORES-200) that require a Hugging Face token.
    """
    os.makedirs(output_dir, exist_ok=True)
    print(f"--- Downloading Gated Datasets to {output_dir} ---")
    
    try:
        login(token=hf_token)
        print("Successfully logged in to Hugging Face.")
    except Exception as e:
        print(f"Failed to log in to Hugging Face: {e}")
        return

    # FLORES-200 (Evaluation Parallel Data)
    print("\n[1/1] Downloading FLORES-200 (Marathi)...")
    try:
        # Note: You must have accepted the terms on the dataset page: 
        # https://huggingface.co/datasets/facebook/flores
        flores = load_dataset("facebook/flores", "mar_Deva-eng_Latn", split="dev")
        flores_path = os.path.join(output_dir, "flores_200_mr")
        flores.save_to_disk(flores_path)
        print(f"Successfully saved FLORES-200 to {flores_path}")
    except Exception as e:
        print(f"Failed to load FLORES-200: {e}")
        print("Make sure your token is correct and you have accepted the dataset terms on Hugging Face.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download gated Marathi datasets.")
    parser.add_argument("--output_dir", type=str, default="./data", help="Directory to save the datasets")
    parser.add_argument("--token", type=str, required=True, help="Hugging Face API Token")
    args = parser.parse_args()
    
    download_gated_datasets(args.output_dir, args.token)
