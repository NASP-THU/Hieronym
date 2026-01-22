import json
import torch
import argparse
from sentence_transformers import SentenceTransformer
from pathlib import Path

EMBEDDING_MODEL_PATH = 'Qwen/Qwen3-Embedding-8B'
SIMILARITY_THRESHOLD = 0.7

def load_model():
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    return SentenceTransformer(EMBEDDING_MODEL_PATH, trust_remote_code=True, device=device)

def compute_similarity(model, text1, text2):
    embeddings = model.encode([text1, text2], convert_to_tensor=True, normalize_embeddings=True)
    return float(torch.dot(embeddings[0], embeddings[1]))

def process_file(model, input_path, output_path):
    with open(input_path) as f:
        data = json.load(f)
    
    results = []
    for item in data:
        gt = item.get('ground_truth', '').strip()
        pred = item.get('prediction', '').strip()
        
        score = max(0.0, min(1.0, compute_similarity(model, gt, pred)))
        results.append({
            "ground_truth": gt,
            "prediction": pred, 
            "similarity_score": round(score, 4),
            "similarity": score >= SIMILARITY_THRESHOLD
        })
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    return len(results), sum(r['similarity'] for r in results)

def main(args):

    model = load_model()
    total, similar = process_file(model, args.input_file, args.output_file)
    
    print(f"Processed {total} entries. Similar: {similar} ({similar/total:.2%})")
    print(f"Results saved to: {args.output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Function name correctness evaluation based on Qwen3-embedding')
    parser.add_argument('-i', '--input_file', type=str, required=True,
                        help='Input file containing predicted function names and ground truth.')
    parser.add_argument('-o', '--output_file', type=str, required=True,
                        help='Output path for processed results')
    args = parser.parse_args()

    main(args)
