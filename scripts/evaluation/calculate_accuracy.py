import json
import argparse
import os
import re

TEXT_NORMALIZATION_PATTERN = re.compile(r'^_+|_+$|_{2,}')

def canonicalize_text(text):
    """Normalize text by trimming and consolidating underscores"""
    if not text:
        return ""
    normalized = text.strip().lower()
    return TEXT_NORMALIZATION_PATTERN.sub('_', normalized)

def load_evaluation_results(file_path):
    """Load and validate evaluation results file"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    if not isinstance(data, list):
        raise ValueError(f"File {file_path} must contain a JSON array")
    
    results_index = {}
    for item in data:
        gt = item.get("ground_truth", "").strip()
        if not gt:
            continue
        
        norm_gt = canonicalize_text(gt)
        is_similar = item.get("similarity", False)
        
        if isinstance(is_similar, str):
            is_similar = is_similar.lower().strip() == "true"
        
        results_index[norm_gt] = bool(is_similar)
    
    return results_index

def compare_model_agreements(chatgpt_results, qwen_results):
    """Compare agreement between two model evaluations"""
    common_ground_truths = set(chatgpt_results.keys()) & set(qwen_results.keys())
    
    both_true_count = 0
    for gt in common_ground_truths:
        if chatgpt_results[gt] and qwen_results[gt]:
            both_true_count += 1
    
    total_comparable = len(common_ground_truths)
    both_true_ratio = both_true_count / total_comparable if total_comparable > 0 else 0
    
    return {
        "common_entries": total_comparable,
        "both_true_count": both_true_count,
        "agreement_ratio": both_true_ratio,
        "chatgpt_unique": len(chatgpt_results) - total_comparable,
        "qwen_unique": len(qwen_results) - total_comparable
    }

def main(args):
    try:
        chatgpt_results = load_evaluation_results(args.input_chatgpt_file)
        qwen_results = load_evaluation_results(args.input_qwen_file)
        
        stats = compare_model_agreements(chatgpt_results, qwen_results)
        
        print(f"Common entries evaluated by both models: {stats['common_entries']}")
        print(f"Entries where both models agree on similarity: {stats['both_true_count']}")
        print(f"Agreement ratio: {stats['agreement_ratio']:.2%}")
        print(f"ChatGPT unique entries: {stats['chatgpt_unique']}")
        print(f"Qwen unique entries: {stats['qwen_unique']}")
        
    except Exception as e:
        print(f"Error: {str(e)}")
        exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Compare agreement between ChatGPT and Qwen3 embedding model evaluations')
    parser.add_argument('--input_chatgpt_file', type=str, required=True, help='Path to ChatGPT evaluation results (JSON)')
    parser.add_argument('--input_qwen_file', type=str, required=True, help='Path to Qwen3 embedding evaluation results (JSON)')
    args = parser.parse_args()

    main(args)