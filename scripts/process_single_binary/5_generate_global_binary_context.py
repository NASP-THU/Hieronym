import asyncio
import aiohttp
import json
import os
import time
import argparse
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from asyncio import Semaphore

# Configuration
VLLM_API_URL = "http://localhost:8080/v1/chat/completions"
VLLM_MODEL_NAME = "qwen3-coder"
MAX_CONCURRENT_REQUESTS = 16
TIMEOUT = 60

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

async def process_function_async(
    bin_name: str,
    bin_info: Dict[str, Any],
    client: AsyncVLLMClient,
) -> Optional[Dict[str, Any]]:

    if len(bin_info['strings_out']) > 100:
        string_out = "\n".join(bin_info['strings_out'][:50] + bin_info['strings_out'][-50:])
    else:
        string_out = "\n".join(bin_info['strings_out'])
    ldd_out = bin_info['ldd_output']
    file_info = bin_info['file_output'].split(',')[:-1]
    file_out = ','.join(file_info)

    prompt = f"""**Role:** You are an expert reverse engineering assistant specializing in binary analysis.

                **Task:** Synthesize the provided static analysis data into a concise, semantic summary of a third-party library binary. Your summary will provide the critical context needed for the accurate semantic recovery of function names in a later phase of the analysis.

                **Summary Instructions:**
                - **Content:** Analyze the provided data to describe the program's core functionality and purpose. Your summary must be an inference-based synthesis, highlighting:
                    - **Core Capabilities:** The principal algorithms, cryptographic routines, or protocols it implements.
                    - **Key Behavior:** Its main operational logic, data handling patterns, and communication methods.
                    - **System Interaction:** Significant interactions with the operating system, libraries, or hardware, and any standard error codes it uses.
                - **Format:** Limit each output to a single and self-contained paragraph of no more than 150 words.
                - **Constraints:** Do not include code blocks, JSON, raw data dumps, compilation details, or any meta-commentary. Provide only the final, polished summary.
                
                **Input Data:**
                The static analysis information for the binary will be provided in the following format:
                - **Strings:** {string_out}
                - **LDD:** {ldd_out}
                - **File:** {file_out}
                
                **Output:**
                Generate the final descriptive summary directly.
                """

    messages = [
        {
            "role": "user",
            "content": prompt,
        },
    ]
    res = await client.query(messages, max_tokens=256)
    if res.error:
        print(f"[Error] {bin_name}: {res.error}")
    bin_data = {}
    bin_data[bin_name] = res.text

    return bin_data

async def process_batch_async(
    func_batch: List[Dict],
    max_concurrent: int = 16
) -> List[Dict[str, Any]]:
    results = {}
    async with AsyncVLLMClient(max_concurrent=max_concurrent) as client:
        tasks = [
            process_function_async(name, func, client)
            for (name, func) in func_batch
        ]
        individual_results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in individual_results:
            if isinstance(r, Exception):
                print(f"[Task Error] {r}")
            elif r is not None:
                results.update(r)
    return results

async def main_async(
    input_file: str,
    output_file: str,
    batch_size: int = 16,
    max_concurrent_per_batch: int = 16,
):
    final_results = {}

    with open(input_file, 'r', encoding='utf-8') as f:
        source_binaries = json.load(f)

    datasets = []
    for bin_item in source_binaries.items():
        datasets.append(bin_item)

    batches = [
        datasets[i:i + batch_size]
        for i in range(0, len(datasets), batch_size)
    ]

    print(f"[+] Starting async processing of {len(batches)} batches...")

    start_time = time.time()
    for i, batch in enumerate(batches):
        print(f"[Batch {i+1}/{len(batches)}] Processing {len(batch)} functions...")
        batch_results = await process_batch_async(
            batch,
            max_concurrent=max_concurrent_per_batch
        )
        final_results.update(batch_results)

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(final_results, f, ensure_ascii=False, indent=4)

    elapsed = time.time() - start_time
    print(f"[+] Done! Processed {len(final_results)} functions in {elapsed:.2f}s.")
    print(f"    Avg: {len(final_results)/elapsed:.2f} funcs/sec")
    print(f"    Results saved to: {output_file}")

def main(
    input_file,
    output_file,
    max_workers=4,
    batch_size=16,
):

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
    parser = argparse.ArgumentParser(description='Process input file to generate global binary context.')
    parser.add_argument("-i", "--input_file", type=str, required=True,
                        help="Path to the input file for processing.")
    parser.add_argument("-o", "--output_file", type=str, required=True,
                        help="Path where the processed output will be saved.")
    parser.add_argument("--max_workers", type=int, default=32,
                        help="Maximum number of worker processes (default: 32).")
    parser.add_argument("--batch_size", type=int, default=32,
                        help="Number of items per processing batch (default: 32).")

    args = parser.parse_args()

    main(input_file=args.input_file, output_file=args.output_file, max_workers=args.max_workers, batch_size=args.batch_size)