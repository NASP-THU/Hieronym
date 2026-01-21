#!/usr/bin/env python3
import os
import json
import asyncio
from typing import List, Dict, AsyncGenerator
import aiohttp
from tqdm import tqdm
import argparse
import time
from pathlib import Path

VLLM_API_URL = "http://localhost:8081/v1/chat/completions"

class VLLMAPIClient:
    def __init__(self, api_url: str = VLLM_API_URL, lora_name: str = "lora", max_concurrent: int = 16, timeout: int = 600):
        self.api_url = api_url
        self.lora_name = lora_name
        self.timeout = aiohttp.ClientTimeout(total=3600)

    async def generate(self, messages: List[Dict[str,str]], params: dict) -> str:
        #async with self.semaphore:
            try:
                payload = {
                    "model": self.lora_name,
                    "messages": messages,
                    "temperature": params.get("temperature", 0.1),
                    "top_p": params.get("top_p", 0.7),
                    "max_tokens": params.get("max_tokens", 512),
                    "stream": False,
                }

                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.post(
                            f"{self.api_url}",
                            json=payload,
                            headers={"Content-Type": "application/json"}
                    ) as response:
                        result = await response.json()
                        return result["choices"][0]["message"]["content"]

            except Exception as e:
                return ""


async def process_conversation_turn(client, conv, progress=None):

    history = []

    history.append({"role": "user", "content": conv[0]['content']})

    final_output = await client.generate(history, {
                "max_tokens": 64,
                "temperature": 0.1,
                "top_p": 0.75,
                "top_k": 40,
    })

    final_output = final_output.strip().replace('assistant: ', '')

    if progress:
        progress.update(1)

    return {
        "ground_truth": conv[-1]["content"],
        "predicted_name": final_output
    }


async def worker(task_queue, result_queue, max_concurrent: int = 16):
    client = VLLMAPIClient(max_concurrent=max_concurrent)

    while True:
        task = await task_queue.get()
        if task is None:
            break

        idx, conv = task
        try:
            result = await process_conversation_turn(client, conv)
            await result_queue.put((idx, {
                **result,
                'original_index': idx
            }))
        except Exception as e:
            print(f"Error processing task {idx}: {str(e)}")
            await result_queue.put((idx, None))

async def main(args):


    with open(args.output_file, 'w') as f:
        json.dump([], f, indent=4)

    with open(args.input_file, 'r', encoding='utf-8') as f:
        testset = json.load(f)


    test_datasets = list(enumerate(testset))

    batch_size = getattr(args, 'batch_size', 16)
    max_concurrent = min(args.num_workers, batch_size)
    batches = [
        test_datasets[i:i + batch_size]
        for i in range(0, len(test_datasets), batch_size)
    ]

    print(f"[+] Starting async processing of {len(batches)} batches (batch_size={batch_size})...")
    start_time = time.time()
    final_results = []


    for batch_num, batch in enumerate(batches, 1):
        print(f"[Batch {batch_num}/{len(batches)}] Processing {len(batch)} conversations...")

        task_queue = asyncio.Queue()
        result_queue = asyncio.Queue()

        for idx, conv in batch:
            await task_queue.put((idx, conv))

        for _ in range(max_concurrent):
            await task_queue.put(None)

        workers = [
            asyncio.create_task(worker(task_queue, result_queue))
            for _ in range(max_concurrent)
        ]


        batch_results = [None] * len(batch)
        with tqdm(total=len(batch), desc=f"Batch {batch_num}") as progress:
            received = 0
            while received < len(batch):
                idx, result = await result_queue.get()
                if result is not None:

                    result['original_index'] = idx
                    batch_results[received] = result
                    received += 1
                    progress.update(1)

        await asyncio.gather(*workers)

        final_results.extend([r for r in batch_results if r is not None])
        with open(args.output_file, 'w', encoding='utf-8') as f:
            json.dump(final_results, f, ensure_ascii=False, indent=4)

        batch_time = time.time() - start_time
        print(f"[Batch {batch_num}] Completed. Speed: {len(batch) / batch_time:.2f} convs/sec")

    elapsed = time.time() - start_time
    print(f"[+] Done! Processed {len(final_results)} conversations in {elapsed:.2f}s")
    print(f"    Average speed: {len(final_results) / elapsed:.2f} convs/sec")
    print(f"    Results saved to: {args.output_file}")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process test set data and generate predictions.')
    parser.add_argument("--input-file", type=str, default="", required=True,
                        help='Path to the formatted test set file.')
    parser.add_argument("--output-file", type=str, default="", required=True,
                        help='Path to save the predicted output.')
    parser.add_argument("--num-workers", type=int, default=64,
                        help='Number of parallel worker processes (default: 64).')
    parser.add_argument('--batch-size', type=int, default=64,
                        help='Number of items per batch (default: 64).')
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    asyncio.run(main(args))
