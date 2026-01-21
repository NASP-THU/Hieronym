import os
import json
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from tqdm import tqdm
import numpy as np
import torch
from torch.multiprocessing import set_start_method
from transformers import RobertaTokenizer, RobertaModel
from sklearn.metrics.pairwise import cosine_similarity
from tree_sitter import Language, Parser
import tree_sitter_cpp as tscpp
from multiprocessing import Manager
import argparse
import re

# Initialize multiprocessing
try:
    set_start_method('spawn', force=True)
except RuntimeError:
    pass


def init_parser():
    cpp_language = Language(tscpp.language())
    parser = Parser(cpp_language)
    return parser


def init_codebert():
    tokenizer = RobertaTokenizer.from_pretrained("codebert-base")
    model = RobertaModel.from_pretrained("codebert-base", weights_only=False)
    model.eval()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    return tokenizer, model, device


def extract_structure_paths(code: str):
    try:
        parser = init_parser()
        tree = parser.parse(bytes(code, 'utf-8'))
        root = tree.root_node
        paths = []

        def dfs(node, path):
            if node.type in {'comment', 'preproc_def', 'preproc_include'}:
                return
            new_path = path + [node.type]
            if not node.children:
                paths.append(">".join(new_path))
            else:
                for child in node.children:
                    dfs(child, new_path)

        dfs(root, [])
        return paths
    except Exception as e:
        print(f"[extract_structure_paths] Parse error: {e}")
        return []


class SharedCache:
    def __init__(self, manager):
        self.paths_cache = manager.dict()
        self.embeds_cache = manager.dict()
        self.lock = manager.Lock()

    def get_paths(self, code):
        with self.lock:
            return self.paths_cache.get(code)

    def set_paths(self, code, paths):
        with self.lock:
            self.paths_cache[code] = paths

    def get_embed(self, code):
        with self.lock:
            return self.embeds_cache.get(code)

    def set_embed(self, code, embed):
        with self.lock:
            self.embeds_cache[code] = embed


def process_batch(batch_data, cache_manager):
    tokenizer, model, device = init_codebert()
    results = []

    for data in batch_data:
        block_code = data['code']
        start_line, end_line = data['code_range']

        # Get or compute structure paths
        paths = cache_manager.get_paths(block_code)
        if paths is None:
            paths = extract_structure_paths(block_code)
            cache_manager.set_paths(block_code, paths)

        # Get or compute embeddings
        embed = cache_manager.get_embed(block_code)
        if embed is None:
            inputs = tokenizer(block_code, return_tensors="pt", truncation=True, max_length=256, padding=True).to(device)
            with torch.no_grad():
                outputs = model(**inputs)
                embed = outputs.last_hidden_state[:, 0, :].cpu().numpy()
            cache_manager.set_embed(block_code, embed)

        results.append({
            'id': data['id'],
            'type': data['type'],
            'paths': paths,
            'embed': embed,
            'code': block_code
        })

    return results


def batch_process(all_blocks, cache_manager, batch_size=1000, num_processes=8):
    processed_blocks = []
    for func_name, func_type, blocks in all_blocks:
        try:
            code = list(blocks.values())[-1]['code']
        except:
            code = blocks['code']

        code_lines = code.split('\n')

        for block_id, block in blocks.items():
            if block['type'] != 'full_function' and block['type'] != 'function_definition':
                start_line, end_line = block['code']
                block_code = '\n'.join(code_lines[start_line:end_line])
                processed_blocks.append({
                    'id': f"{func_name}_{func_type}_{block_id}",
                    'type': block['type'],
                    'code': block_code,
                    'code_range': block['code']
                })

    batches = [processed_blocks[i:i + batch_size] for i in range(0, len(processed_blocks), batch_size)]

    with ProcessPoolExecutor(max_workers=num_processes) as executor:
        results = list(tqdm(
            executor.map(partial(process_batch, cache_manager=cache_manager), batches),
            total=len(batches),
            desc="Processing code blocks"
        ))

    return [item for sublist in results for item in sublist]


def jaccard_similarity(a, b):
    set_a, set_b = set(a), set(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def extract_strcmp_keyword(code):
    match = re.search(r'strcmp\([^,]+,\s*\"([^\"]+)\"\)', code, re.IGNORECASE)
    return match.group(1).lower() if match else None


def is_code_match(code1, code2):
    keyword1 = extract_strcmp_keyword(code1)
    keyword2 = extract_strcmp_keyword(code2)
    return keyword1 is not None and keyword1 == keyword2


def match_blocks(unstripped_blocks, stripped_blocks, threshold=0.3, alpha=0.5):
    matches = {}
    used_stripped_ids = set()

    unstripped_data = {b['id']: b for b in unstripped_blocks if b['type'] != 'full_function' and b['type'] != 'function_definition'}
    stripped_data = {b['id']: b for b in stripped_blocks if b['type'] != 'full_function' and b['type'] != 'function_definition'}

    for unstripped_id, unstripped_block in unstripped_data.items():
        best_score, best_stripped_id = 0.0, None

        for stripped_id, stripped_block in stripped_data.items():
            if stripped_id in used_stripped_ids:
                continue
            if stripped_block['type'] != unstripped_block['type']:
                continue

            has_keyword_match = is_code_match(unstripped_block['code'], stripped_block['code'])
            structure_score = jaccard_similarity(unstripped_block['paths'], stripped_block['paths'])
            semantic_score = cosine_similarity(unstripped_block['embed'], stripped_block['embed'])[0][0]
            
            if has_keyword_match:
                final_score = 0.9
            else:
                final_score = alpha * structure_score + (1 - alpha) * semantic_score

            if final_score > best_score:
                best_score = final_score
                best_stripped_id = stripped_id

        if best_stripped_id and best_score >= threshold:
            matches[unstripped_id] = {
                'stripped_block_id': best_stripped_id,
                'unstripped_block_id': unstripped_id,
                'final_score': float(round(best_score, 4)),
                'structure_score': float(round(structure_score, 4)),
                'semantic_score': float(round(semantic_score, 4)),
                'unstripped_type': unstripped_block['type'],
                'stripped_type': stripped_data[best_stripped_id]['type'],
                'unstripped_code': unstripped_block['code'],
                'stripped_code': stripped_data[best_stripped_id]['code']
            }
            used_stripped_ids.add(best_stripped_id)

    return matches


def process(input_file, alpha=0.5, threshold=0.3, batch_size=1000, num_processes=8):
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    with Manager() as manager:
        cache_manager = SharedCache(manager)

        all_blocks = []
        aligned_result = {}
        for func, content in data.items():
            if "unstripped" in content:
                all_blocks.append((func, "unstripped", content["unstripped"]))
            if "stripped" in content:
                all_blocks.append((func, "stripped", content["stripped"]))
                aligned_result[func] = {
                    'stripped_block_code': content['stripped'][func]['block_code'],
                    'stripped_code': content['stripped'][func]['code']
                }

        processed_blocks = batch_process(all_blocks, cache_manager, batch_size, num_processes)

        processed_data = {}
        for block in processed_blocks:
            func_parts = block['id'].split('_')[:-2]
            block_id = block['id'].split('_')[-1]
            func_name = '_'.join(func_parts)
            if func_name not in processed_data:
                processed_data[func_name] = {'unstripped': {}, 'stripped': {}}

            block_type = 'unstripped' if 'unstripped' in block['id'] else 'stripped'
            block['id'] = block_id
            processed_data[func_name][block_type][block_id] = block

        for func, content in tqdm(processed_data.items(), desc="Matching functions"):
            if "unstripped" in content and "stripped" in content:
                aligned = match_blocks(
                    list(content["unstripped"].values()),
                    list(content["stripped"].values()),
                    threshold,
                    alpha
                )
                if aligned:
                    aligned_result[func].update(aligned)

        output_file = os.path.join(os.path.dirname(input_file), "ast_semantic_alignment.json")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(aligned_result, f, indent=2, ensure_ascii=False)

        print(f"\n AST+semantic alignment completed, results saved to: {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AST+Semantic code block alignment tool")
    parser.add_argument("--input", type=str, required=True, help="Input JSON file path")
    parser.add_argument("--alpha", type=float, default=0.8, help="Structure similarity weight")
    parser.add_argument("--threshold", type=float, default=0.3, help="Minimum similarity threshold")
    parser.add_argument("--batch_size", type=int, default=32, help="CodeBERT inference batch size")
    args = parser.parse_args()
    process(args.input, alpha=args.alpha, threshold=args.threshold, batch_size=args.batch_size)