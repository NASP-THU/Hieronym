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
from typing import Dict, Any, Iterator, List, Tuple
import re
from cxxfilt import demangle


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
                    "temperature": 0.0,
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
        new_name = data["new_name"]
        return new_name
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{[\s\S]*\}', response)
    if match:
        try:
            data = json.loads(match.group(0))
            new_name = data["new_name"]
            return new_name
        except json.JSONDecodeError:
            pass

        try:
            match_str = match.group(0)
            if match_str.startswith('{'):
                match_str = match_str.strip('{').strip()
            if match_str.endswith('}'):
                match_str = match_str.strip('}').strip()
            tmp = match_str.replace("\"new_name\":", "")
            return tmp.strip()
        except:
            print(match.group(0).replace("\"new_name\":", ""))
    print(response.replace("{\n    \"new_name\":", ""))

    return response.replace("{\n    \"new_name\":", "")



async def process_function_async(
    function_name: tuple,
    client: AsyncVLLMClient,
    config_dict: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    system_prompt = config_dict['system_prompt']
    
    ground_truth, prediction = function_name
    if len(prediction)>0:
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": prediction,
            },
        ]
        try:
            res = await client.query(messages, max_tokens=128)
            if res.error:
                print(f"{prediction}: {res.error}")

            output = res.text.strip()
            output = output.strip('```json').strip('```').strip()

            new_name = extract_and_parse_json(output)
            if len(new_name) > 60:
                new_name = prediction
        except:
            new_name = prediction
    else:
        new_name=' '

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": ground_truth,
        },
    ]
    try:
        res = await client.query(messages, max_tokens=128)
        if res.error:
            print(f"{ground_truth}: {res.error}")

        output = res.text.strip()
        output = output.strip('```json').strip('```').strip()

        new_ground = extract_and_parse_json(output)
        if len(new_ground) > 60:
            new_ground = ground_truth
    except:
        new_ground = ground_truth

    if ground_truth=='null':
        new_ground = ground_truth
    func_data = [(ground_truth, new_ground, prediction, new_name)]

    return func_data if len(func_data) > 0 else None


async def process_batch_async(
    func_batch: List[str],
    config_dict: Dict[str, Any],
    max_concurrent: int = 16
) -> Dict[str, Any]:
    results = []
    async with AsyncVLLMClient(max_concurrent=max_concurrent) as client:
        tasks = [
            process_function_async(func_name, client, config_dict)
            for func_name in func_batch
        ]
        individual_results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in individual_results:
            if isinstance(r, Exception):
                print(f"[Task Error] {r}")
                print(r)
            elif r is not None:
                results.extend(r)
    return results


def func_name_preprocessing(func_name):
    """
    Preprocess function name by:
        - tokenize whole name into words
        - remove digits
    """
    func_name = func_name.lower()
    # split whole name into words and remove digits

    func_name = func_name.replace('_', ' ')
    
    tmp = ''
    for c in func_name:
        if not c.isalpha():  # filter out numbers and other special characters, e.g. '_' and digits
            tmp = tmp + ' '
        elif c.isupper():
            tmp = tmp + ' ' + c
        else:
            tmp = tmp + c
    tmp = tmp.strip()
    res = tmp.split(' ')

    words = []
    for word in res:
        if not isinstance(word, str) or word == '':
            continue
        words.append(word)

    resulting_name = ' '.join(words)
    return resulting_name.lower()



def demangle_in_text(text):

    mangled_pattern = r'(_Z[\w\d]+)'
    results = ''

    for match in re.finditer(mangled_pattern, text):
        mangled = match.group(1)
        try:
            demangled = demangle(mangled)
            try:
                results = demangled.split('(')[0]
            except:
                results = demangled
        except:
            continue

    if len(results)==0:
        results = text
    return results


def batched_functions(results):
    datasets = []

    for result in results:

        ground_truth = result['ground_truth'].strip().split(':')[-1]
        ground_truth = ground_truth.replace('.', '').strip()

        if ground_truth is None:
            continue

        prediction = result['predicted_name'].split('\n')[0]

        prediction = prediction.split('.')[0]

        prediction = prediction.split(':')[-1]
        if prediction.find('\"') != -1:
            prediction = prediction.split('\"')[1]
        if prediction.find('`') != -1:
            prediction = prediction.split('`')[1]
        prediction = prediction.split('.')[-1].strip()
        prediction = prediction.split(' ')[-1].strip()

        prediction = demangle_in_text(prediction)

        datasets.append((ground_truth, prediction))

    return datasets


async def main_async(
    input_file: str,
    output_file: str,
    batch_size: int = 16,
    max_concurrent_per_batch: int = 16,
):
     
    
    with open(input_file, 'r', encoding='utf-8') as f:
        source_functions = json.load(f)

    source_functions = batched_functions(source_functions)

    system_prompt = ""
    prompt_path = './evaluation/prompt/name_tokenization'
    if os.path.exists(prompt_path):
        with open(prompt_path, 'r', encoding='utf-8') as f:
            system_prompt = ''.join(f.readlines())
    else:
        raise FileNotFoundError(f"Prompt file not found at {prompt_path}")


    config_dict = {
        'system_prompt': system_prompt,
    }

    batches = [
        source_functions[i:i + batch_size]
        for i in range(0, len(source_functions), batch_size)
    ]


    print(f"[+] Starting async processing of {len(batches)} batches...")
    final_results = []
    start_time = time.time()
    for i, batch in enumerate(batches):
        print(f"[Batch {i+1}/{len(batches)}] Processing {len(batch)} functions...")
        batch_results = await process_batch_async(
            batch, config_dict,
            max_concurrent=max_concurrent_per_batch
        )
        final_results.extend(batch_results)

        with open(output_file+'.json', 'w', encoding='utf-8') as f:
            json.dump(final_results, f, ensure_ascii=False, indent=4)


    file = open(output_file+'.txt', 'w')
    file.write('')
    file.close()

    for g, ground_truth, p, prediction in final_results:

        ground_truth = func_name_preprocessing(ground_truth)
        prediction = func_name_preprocessing(prediction)

        file = open(output_file+'.txt', 'a')
        file.write(ground_truth + ', ' + prediction + ',\n')
        file.close()


    elapsed = time.time() - start_time
    print(f"[+] Done! Processed {len(final_results)} functions in {elapsed:.2f}s.")
    print(f"    Avg: {len(final_results)/elapsed:.2f} funcs/sec")
    print(f"    Results saved to: {output_file}")


def main(
    input_file,
    output_dir,
    max_workers=4,
    batch_size=16,
):
    output_file = os.path.join(output_dir, 'evaluation')
    temp_dir = os.path.dirname(output_file)
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    asyncio.run(main_async(
        input_file=input_file,
        output_file=output_file,
        batch_size=batch_size,
        max_concurrent_per_batch=max_workers,

    ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Preprocess the predicted function name for evaluation.')
    parser.add_argument('-i', '--input_file', type=str, required=True,
                        help='Input file containing predicted function names and ground truth.')
    parser.add_argument('-o', '--output_dir', type=str, required=True,
                        help='Directory to save evaluation results.')
    parser.add_argument("--max_workers", type=int, default=64,
                        help='Maximum number of worker processes (default: 64).')
    parser.add_argument("--batch_size", type=int, default=64,
                        help='Batch size for processing (default: 64).')
    args = parser.parse_args()


    main(input_file=args.input_file, output_dir=args.output_dir, max_workers=args.max_workers, batch_size=args.batch_size)
