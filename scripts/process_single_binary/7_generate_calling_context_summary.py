import asyncio
import aiohttp
import json
import os
import re
import time
import argparse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# ================== Configuration ==================
# This setting for test
VLLM_API_URL = "http://localhost:8081/v1/chat/completions"
VLLM_MODEL_NAME = " "     # Model Name，eg: "codellama-lora"
TIMEOUT = 600

# =============================================

@dataclass
class ModelResponse:
    text: str
    token_count: int
    error: Optional[str] = None


class AsyncVLLMClient:
    """Async vLLM client supporting high concurrency."""

    def __init__(self, api_url: str = VLLM_API_URL, model: str = VLLM_MODEL_NAME, max_concurrent: int = 16):
        self.api_url = api_url
        self.model = model
        self.semaphore = asyncio.Semaphore(max_concurrent)
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
                    text = await resp.text()
                    return ModelResponse(text="", token_count=0, error=f"HTTP {resp.status}: {text}")
            except Exception as e:
                return ModelResponse(text="", token_count=0, error=f"Request failed: {e}")


def extract_and_parse_json(response: str) -> str:
    """Extract and parse JSON from model response."""
    try:
        data = json.loads(response)
        return data["code_description"]
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{[\s\S]*\}', response)
    if match:
        try:
            data = json.loads(match.group(0))
            return data["code_description"]
        except json.JSONDecodeError:
            pass

        try:
            match_str = match.group(0)
            if match_str.startswith('{'):
                match_str = match_str.strip('{').strip()
            if match_str.endswith('}'):
                match_str = match_str.strip('}').strip()
            tmp = match_str
            if not tmp.endswith("\""):
                tmp += "\""

            return tmp
        except:
            pass

    return response.strip()

async def process_function_async(
        function_item: Tuple[str, Dict],
        client: AsyncVLLMClient,
        config_dict: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    function_name, func_body = function_item

    function_data = {function_name: {"target": func_body['output']}}
    function_system_prompt = config_dict["function_system_prompt"]

    caller_descriptions = []
    for item in func_body["caller"]:
        if isinstance(item, str):
            caller_descriptions.append(item)
        else:
            caller_name = next(iter(item.keys()))
            caller_code = next(iter(item.values()))
            messages = [
                {"role": "system", "content": function_system_prompt},
                {"role": "user", "content": caller_code},
            ]
            res = await client.query(messages, max_tokens=400)
            if res.error:
                print(f"[Func Error] {function_name}: {res.error}")
                continue
            output = res.text.strip().strip("```json").strip("```").strip()
            description = extract_and_parse_json(output)
            caller_descriptions.append({caller_name: description})

    callee_descriptions = []
    for item in func_body["callee_internal"]:
        if isinstance(item, str):
            callee_descriptions.append({item: item})
        else:
            callee_name = next(iter(item.keys()))
            callee_code = next(iter(item.values()))
            messages = [
                {"role": "system", "content": function_system_prompt},
                {"role": "user", "content": callee_code},
            ]
            res = await client.query(messages, max_tokens=400)
            if res.error:
                print(f"[Func Error] {function_name}: {res.error}")
                continue
            output = res.text.strip().strip("```json").strip("```").strip()
            description = extract_and_parse_json(output)
            callee_descriptions.append({callee_name: description})

    code = func_body.get("stripped")
    function_data[function_name].update({
        "bin_info": func_body["bin_info"],
        "input": code,
        "caller": caller_descriptions,
        "callee_internal": callee_descriptions,
        "callee_external": func_body["callee_external"],
    })

    return function_data if len(function_data[function_name]) > 1 else None


async def process_batch_async(
        function_batch: List[Tuple[str, Dict]],
        config_dict: Dict[str, Any],
        max_concurrent: int = 16
) -> List[Dict[str, Any]]:
    """Process batch of functions concurrently."""
    results = []
    async with AsyncVLLMClient(max_concurrent=max_concurrent) as client:
        tasks = [
            process_function_async(func, client, config_dict)
            for func in function_batch
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in batch_results:
            if isinstance(result, Exception):
                print(f"[Task Error] {result}")
            elif result is not None:
                results.append(result)
    return results


def batched_stripped_functions(source_functions: Dict) -> List[Tuple[str, Dict]]:

    functions_list = []
    
    for item in source_functions:
        for func_name, merge_data in item.items():
            functions_list.append((func_name, merge_data))
    return functions_list


async def main_async(
        input_file: str,
        output_file: str,
        batch_size: int = 16,
        max_concurrent_per_batch: int = 16,
) -> None:
    """Main async processing pipeline."""
    with open(input_file, "r", encoding="utf-8") as f:
        source_functions = json.load(f)
    source_functions = batched_stripped_functions(source_functions)


    prompt_path = "./summary_generate/utils/prompt_calling_context_for_prediction"
    if not os.path.exists(prompt_path):
        raise FileNotFoundError(f"Prompt file not found at {prompt_path}")
    with open(prompt_path, "r", encoding="utf-8") as f:
        function_system_prompt = "".join(f.readlines())

    config_dict = {"function_system_prompt": function_system_prompt}

    function_batches = [
        source_functions[i:i + batch_size]
        for i in range(0, len(source_functions), batch_size)
    ]

    print(f"[+] Starting async processing of {len(function_batches)} batches...")
    start_time = time.time()
    final_results = []

    for idx, batch in enumerate(function_batches):
        print(f"[Batch {idx + 1}/{len(function_batches)}] Processing {len(batch)} functions...")
        batch_results = await process_batch_async(
            batch, config_dict, max_concurrent_per_batch
        )
        final_results.extend(batch_results)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(final_results, f, ensure_ascii=False, indent=4)

    elapsed_time = time.time() - start_time
    print(f"[+] Done! Processed {len(final_results)} functions in {elapsed_time:.2f}s.")
    print(f"    Avg: {len(final_results) / elapsed_time:.2f} funcs/sec")
    print(f"    Results saved to: {output_file}")


def main(
        input_file: str,
        output_dir: str,
        max_workers: int = 4,
        batch_size: int = 16,
) -> None:
    """Synchronous entry point."""

    output_file = os.path.join(output_dir, f'data_for_prediction.json')

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    asyncio.run(
        main_async(
            input_file=input_file,
            output_file=output_file,
            batch_size=batch_size,
            max_concurrent_per_batch=max_workers,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Used to generate summaries of internal functions in test set data.')
    parser.add_argument('-i', '--input_file', type=str, required=True,
                        help='Input path for test set file.')
    parser.add_argument('-o', '--output_dir', type=str, required=True,
                        help='Directory to save the output files.')
    parser.add_argument('--max_workers', type=int, default=32,
                        help='Maximum number of worker processes (default: 64).')
    parser.add_argument('--batch_size', type=int, default=32,
                        help='Batch size for processing (default: 64).')
    args = parser.parse_args()

    main(
        input_file=args.input_file,
        output_dir=args.output_dir,
        max_workers=args.max_workers,
        batch_size=args.batch_size,
    )