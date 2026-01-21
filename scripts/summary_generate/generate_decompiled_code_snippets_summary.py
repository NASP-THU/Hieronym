import asyncio
import aiohttp
import json
import os
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from functools import partial
from asyncio import Semaphore
import time
from collections import defaultdict
import argparse
import itertools
import re

# ================== config ==================
VLLM_API_URL = "http://localhost:8080/v1/chat/completions"
VLLM_MODEL_NAME = "qwen3-coder"
MAX_CONCURRENT_REQUESTS = 64
TIMEOUT = 600
# ==========================================

@dataclass
class ModelResponse:
    text: str
    token_count: int
    error: Optional[str] = None

class AsyncVLLMClient:
    def __init__(self, api_url: str = VLLM_API_URL, model: str = VLLM_MODEL_NAME, max_concurrent: int = 16):
        self.api_url = api_url
        self.model = model
        self.semaphore = Semaphore(max_concurrent)
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def query(self, messages: List[Dict[str, str]], max_tokens: int = 512) -> ModelResponse:
        async with self.semaphore:
            try:
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                    "top_p": 0.75,
                    "top_k": 40,
                    "stop": [],
                }
                async with self.session.post(self.api_url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data["choices"][0]["message"]["content"]
                        tokens = data.get("usage", {}).get("completion_tokens", len(text.split()))
                        return ModelResponse(text=text.strip(), token_count=tokens)
                    else:
                        text = await resp.text()
                        return ModelResponse(text="", token_count=0, error=f"HTTP {resp.status}: {text}")
            except Exception as e:
                return ModelResponse(text="", token_count=0, error=f"Request failed: {e}")


def extract_and_parse_json(response):
    try:
        data = json.loads(response)
        description = data["code_description"]
        return description
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{[\s\S]*\}', response)
    if match:
        try:
            data = json.loads(match.group(0))
            description = data["code_description"]
            return description
        except json.JSONDecodeError:
            pass

        try:
            match_str = match.group(0)
            if match_str.startswith('{'):
                match_str = match_str.strip('{').strip()
            if match_str.endswith('}'):
                match_str = match_str.strip('}').strip()
            tmp = match_str.replace("\"code_description\":", "")
            if not tmp.endswith("\""):
                tmp += "\""
            return tmp
        except:
            print(match.group(0).replace("\"code_description\":", ""))

    return response.replace('{\n    "code_description":', '').strip()

def remove_all_comments(code):
    pattern = r'''
        ("(?:\\.|[^"\\])*")        # Match string
        |(/\*.*?\*/)               # Match /* */ comment
        |(//.*?$)                  # Match // Comment
    '''
    return re.sub(pattern, lambda m: m.group(1) if m.group(1) else '',
                  code, flags=re.DOTALL | re.MULTILINE | re.VERBOSE)

async def process_function_async(
    function_name: str,
    binary_info: str,
    binary_name: str,
    function_data: Dict[str, Any],
    mapping_functions: Dict[str, Any],
    client: AsyncVLLMClient,
    config_dict: Dict[str, Any]
) -> Optional[Dict]:

    function_body = list(function_data.values())[-1]['code'].split('\n')
    mapping_function = mapping_functions.get(function_name) or {}

    result_data = {function_name: {}}
    result_data[function_name]['target'] = function_name.lower()

    function_system_prompt = config_dict['function_system_prompt']
    block_system_prompt = config_dict['block_system_prompt']

    block_history = defaultdict(dict)

    for id, (name, item) in enumerate(function_data.items()):


        if len(function_data) > 1 and name.isdigit():
            start_line, end_line = item.get('code')
            block = '\n'.join(function_body[start_line: end_line])
            block = remove_all_comments(block)
            
            messages = [
                {"role": "system", "content": block_system_prompt},
                {"role": "user", "content": block},
            ]
            res = await client.query(messages, max_tokens=520)
            if res.error:
                continue

            output = res.text.strip()
            output = output.strip('```json').strip('```').strip()
            description = extract_and_parse_json(output)

            try:
                dec_name = mapping_function[name]['stripped_block_id']
                block_history[dec_name] = {'input': mapping_function[name]['stripped_code'], 'output': description}
            except:
                pass

        else:
            block = item['code']
            block = remove_all_comments(block)

            messages = [
                {"role": "system", "content": function_system_prompt},
                {"role": "user", "content": block},
            ]
            res = await client.query(messages, max_tokens=520)
            if res.error:
                continue

            output = res.text.strip()
            output = output.strip('```json').strip('```').strip()
            description = extract_and_parse_json(output)

            result_data[function_name].update({
                'bin_info': binary_info,
                'bin_name': binary_name,
                'history': block_history,
                'input': mapping_function['stripped_code'],
                'output': description,
            })

    return result_data if len(result_data[function_name]) > 1 else None

async def process_batch_async(
    function_batch: List[Tuple],
    mapping_functions: Dict[str, Any],
    config_dict: Dict[str, Any],
    max_concurrent: int = 16
) -> List[Dict[str, Any]]:
    results = []
    async with AsyncVLLMClient(max_concurrent=max_concurrent) as client:
        tasks = [
            process_function_async(list(func.keys())[-1], bin_info, bin_name, func, mapping_functions, client, config_dict)
            for (bin_info, bin_name, func) in function_batch
        ]
        individual_results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in individual_results:
            if isinstance(r, Exception):
                pass
            elif r is not None:
                results.append(r)
    return results



def batched_functions(source_functions, training):
    datasets = []

    if training:
        for func_name, merge_data in source_functions.items():
            datasets.append((merge_data['bin_info'], merge_data['bin_name'], merge_data['unstripped']))

    else:
        for func in source_functions:
            for func_name, merge_data in func.items():
                datasets.append((merge_data['bin_info'], merge_data['bin_name'], merge_data['unstripped']))

    return datasets

async def main_async(
    input_file: str,
    mapping_file: str,
    output_file: str,
    batch_size: int = 16,
    max_concurrent_per_batch: int = 16,
    training: bool = False
):
    final_results = []

    with open(input_file, 'r', encoding='utf-8') as f:
        source_functions = json.load(f)

    source_functions = batched_functions(source_functions, training)

    with open(mapping_file, 'r', encoding='utf-8') as f:
        mapping_functions = json.load(f)

    function_system_prompt = ""
    prompt_path = './summary_generate/utils/prompt_function'
    if os.path.exists(prompt_path):
        with open(prompt_path, 'r', encoding='utf-8') as f:
            function_system_prompt = ''.join(f.readlines())
    else:
        raise FileNotFoundError(f"Prompt file not found at {prompt_path}")

    block_system_prompt = ""
    prompt_path = './summary_generate/utils/prompt_statements'
    if os.path.exists(prompt_path):
        with open(prompt_path, 'r', encoding='utf-8') as f:
            block_system_prompt = ''.join(f.readlines())
    else:
        raise FileNotFoundError(f"Prompt file not found at {prompt_path}")

    config_dict = {
        'function_system_prompt': function_system_prompt,
        'block_system_prompt': block_system_prompt,
    }

    batches = [
        source_functions[i:i + batch_size]
        for i in range(0, len(source_functions), batch_size)
    ]

    start_time = time.time()
    for i, batch in enumerate(batches):
        batch_results = await process_batch_async(
            batch, mapping_functions, config_dict,
            max_concurrent=max_concurrent_per_batch
        )
        final_results.extend(batch_results)

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(final_results, f, ensure_ascii=False, indent=4)

    elapsed = time.time() - start_time

    print(f"[+] Done! Processed {len(final_results)} functions in {elapsed:.2f}s.")
    print(f"    Avg: {len(final_results) / elapsed:.2f} funcs/sec")
    print(f"    Results saved to: {output_file}")

def main(
    input_file,
    mapping_file,
    output_dir,
    max_workers=4,
    batch_size=16,
    training=False
):
    output_file = os.path.join(output_dir, 'function_summary_decompiled.json')

    temp_dir = os.path.dirname(output_file)
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    asyncio.run(main_async(
        input_file=input_file,
        mapping_file=mapping_file,
        output_file=output_file,
        batch_size=batch_size,
        max_concurrent_per_batch=max_workers,
        training=training
    ))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate summaries of code snippets with mapping alignment.')
    parser.add_argument('-i', "--input_file", type=str, required=True,
                        help='Path to the JSON file containing the decompiled code snippets training set.')
    parser.add_argument('-m', "--mapping_file", type=str, required=True,
                        help='File containing aligned statements from stripped and non-stripped symbol tables.')
    parser.add_argument('-o', "--output_dir", type=str, required=True,
                        help='Directory to save the output files.')
    parser.add_argument('--training', type=bool, default=True,
                        help='Flag indicating whether processing training set.')
    parser.add_argument("--max_workers", type=int, default=64,
                        help='Maximum number of worker processes (default: 64).')
    parser.add_argument("--batch_size", type=int, default=64,
                        help='Batch size for processing (default: 64).')


    args = parser.parse_args()

    main(
        input_file=args.input_file,
        mapping_file=args.mapping_file,
        output_dir=args.output_dir,
        max_workers=args.max_workers,
        batch_size=args.batch_size,
        training=args.training
    )