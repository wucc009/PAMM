#!/usr/bin/env python3
import argparse
import sys
import os
from datetime import datetime
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

import torch
import pandas as pd
import numpy as np

MODEL_NAME = "wcc009/PAMM"
DEFAULT_MAX_LENGTH = 250
BATCH_SIZE = 32

def check_dependencies():
    """Check if the dependencies are installed"""
    try:
        import transformers
    except ImportError:
        print("❌  Missing dependencies")
        sys.exit(1)

def auto_download_model():
    """Automatically download the model: check local first, then download, finally prompt manual download if failed"""
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    from huggingface_hub import snapshot_download

    MODEL_DIR = Path(__file__).parent / "model"
    
    # Check if model exists locally first
    if MODEL_DIR.exists() and any(MODEL_DIR.iterdir()):
        # Check if key files exist
        required_files = ['model.safetensors']  
        has_model_file = any((MODEL_DIR / f).exists() for f in required_files)
        
        if has_model_file:
            print(f"📁 Using local model: {MODEL_DIR}")
            return str(MODEL_DIR)
        else:
            print(f"⚠️ Local model directory exists but missing model files, attempting download...")
    else:
        print(f"⚠️ Local model not found, attempting download from HuggingFace...")
    
    # Attempt to download from HuggingFace
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        local_path = snapshot_download(
            repo_id=MODEL_NAME, 
            local_dir=str(MODEL_DIR), 
            local_files_only=False
        )
        print(f"✅  Model downloaded successfully: {local_path}")
        return local_path
        
    except Exception as e:
        print(f"⚠️ Download failed: {e}")
        print(f"\n" + "="*60)
        print("❌  Unable to automatically obtain model")
        print("="*60)
        print(f"\nPlease manually download the model and place it in:")
        print(f"   {MODEL_DIR.absolute()}")
        print(f"\nDownload:")
        print(f"   1. From HuggingFace: https://huggingface.co/{MODEL_NAME}")
        print(f"   2. Ensure directory contains: config.json, model.safetensors, tokenizer.json, etc.")
        print(f"\nThen rerun the program")
        print("="*60)
        sys.exit(1)
            
def validate_input(df):
    # Check if required columns exist
    required_cols = ['ID', 'VH_seq', 'VL_seq']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns: {', '.join(missing_cols)}")
    
    # Check all three columns have values (drop rows with any empty values)
    original_count = len(df)
    mask = (
        df['ID'].notna() & (df['ID'].astype(str).str.strip() != '') &
        df['VH_seq'].notna() & (df['VH_seq'].astype(str).str.strip() != '') &
        df['VL_seq'].notna() & (df['VL_seq'].astype(str).str.strip() != '')
    )
    df = df[mask].copy()
    dropped_missing = original_count - len(df)
    
    if dropped_missing > 0:
        print(f"⚠️ Found {dropped_missing} rows with missing values, removed")
    
    if len(df) == 0:
        raise ValueError("All rows have missing values, no valid data remaining")
    
    # Check VH_seq + VL_seq length <= 247
    df['total_length'] = df['VH_seq'].str.len() + df['VL_seq'].str.len()
    mask_length = df['total_length'] <= 247
    
    dropped_long = (~mask_length).sum()
    if dropped_long > 0:
        print(f"⚠️ Found {dropped_long} rows with sequence length > 247, removed")

    df = df[mask_length].copy()
    df = df.drop(columns=['total_length'])
    
    if len(df) == 0:
        raise ValueError("All rows exceed maximum sequence length, no valid data remaining")
    
    print(f"✅  Validation passed: {len(df)} valid sequences")
    return df

class PAMM:
    def __init__(self, model_path):
        from transformers import AutoTokenizer, AutoModelForSequenceClassification

        # Load model and tokenizer
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"🖥️ Using device: {self.device}")
        model_path = model_path
        print(f"⏳  Loading model...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, do_lower_case=False, use_fast=True, trust_remote_code=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path, num_labels=2, ignore_mismatched_sizes=True, trust_remote_code=True).to(self.device)
        self.model.eval()
        print(f"✅  Model loaded successfully")
    
    def predict_batch(self, vh_seqs, vl_seqs):
        results = []
        
        for i in range(0, len(vh_seqs), BATCH_SIZE):
            vh_batch = vh_seqs[i:i+BATCH_SIZE]
            vl_batch = vl_seqs[i:i+BATCH_SIZE]

            inputs = self.tokenizer(
                vh_batch,
                vl_batch,
                padding='max_length',
                max_length=DEFAULT_MAX_LENGTH,
                truncation=True,
                is_split_into_words=False,
                add_special_tokens=True,
                return_tensors="pt"
            ).to(self.device)
            
            with torch.no_grad():
                outputs = self.model(**inputs)
                probs = torch.softmax(outputs.logits, dim=-1)
            
            for j, prob in enumerate(probs):
                mature_prob = prob[1].item()
                results.append({
                    'Prediction': 'Mature' if mature_prob > 0.5 else 'Naive',
                    'PAMM Score': mature_prob
                })
        return results

def format_output(df, results):
    output_df = pd.DataFrame({
        'ID': df['ID'].values,                         
        'Prediction': [r['Prediction'] for r in results],
        'PAMM Score': [
            round(r['PAMM Score'], 6)
            for r in results
        ]
    })

    return output_df

def main():
    parser = argparse.ArgumentParser(
        description='PAMM (Paired Antibody Maturation Model)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python predict.py example.csv
  
Input file format:
  1. CSV file
  2. Must contain columns: ID, VH_seq (heavy chain variable region sequence), VL_seq (light chain variable region sequence)
  3. No missing values allowed
  4. Combined amino acid length of VH_seq and VL_seq per row must be <= 247
        """
    )
    
    parser.add_argument('input', help='Path to input CSV file')
    args = parser.parse_args()

    # 1. Check dependencies
    check_dependencies()

    # 2. Check model
    model_path = auto_download_model()
    
    # 3. Check input file
    if not os.path.exists(args.input):
        print(f"❌  Error: Input file not found '{args.input}'")
        sys.exit(1)
        
    print(f"📂 Reading input file: {args.input}")
    try:
        df = pd.read_csv(args.input)
        print(f"📊 Total sequences: {len(df)}")
    except Exception as e:
        print(f"❌  Failed to read file: {e}")
        sys.exit(1)
    
    try:
        df = validate_input(df)
    except ValueError as e:
        print(f"❌  {e}")
        sys.exit(1)
    
    # 4. predict
    df['VH_seq'] = df['VH_seq'].apply(lambda seq: ' '.join(list(seq)))
    df['VL_seq'] = df['VL_seq'].apply(lambda seq: ' '.join(list(seq)))
    
    predictor = PAMM(model_path=model_path)

    print(f"🔬 Starting prediction...")
    vh_seqs = df['VH_seq'].tolist()
    vl_seqs = df['VL_seq'].tolist()
    results = predictor.predict_batch(vh_seqs, vl_seqs)
    
    # 5. Format output
    output_df = format_output(df, results)
    
    # 6. Save result
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"result_{timestamp}.csv"
    output_df.to_csv(f"./result/{filename}", index=False)
    print(f"✅  Prediction complete! Results saved to: ./result/{filename}")
    
if __name__ == '__main__':
    main()