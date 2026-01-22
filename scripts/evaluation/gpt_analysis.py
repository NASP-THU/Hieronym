import json
import os
import time
import argparse
from openai import OpenAI
from pathlib import Path

API_KEY = "your_api_key"  # Replace with your OpenAI API key
BASE_URL = "your_base_url"  # Replace with your custom base URL if needed

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    timeout=30.0,
    max_retries=2
)

PROMPT_TEMPLATE = """You are a proficient software analysis assistant specializing in code semantics and binary analysis. Your task is to assess functional similarity between the given two function names.

**Task**
Based on the given context, determine if Function A and Function B are *functionally similar* (i.e., perform equivalent or closely related operations).  
- False = no functional similarity
- True = virtually identical functionality

**Constraints**
- Do not guess; determine based on available evidence.
- Output must be "true" or "false" (NO quotes, brackets, spaces, or additional text).

**Output Format**
boolean value

**Context**
- Language: C
- Binary description: {bin_info}
- Source code: {input}

**Input functions**
- Function A: {ground_truth}
- Function B: {prediction}"""

def process_file(input_path, output_path):
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    results = []
    total = len(data)
    
    for i, item in enumerate(data):
        try:
            prompt = PROMPT_TEMPLATE.format(
                bin_info=item.get("bin_info", ""),
                input=item.get("input", ""),
                ground_truth=item.get("ground_truth", "").strip(),
                prediction=item.get("prediction", "").strip()
            )
            
            response = client.chat.completions.create(
                model="gpt-4o-2024-08-06",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=10
            )
            
            content = response.choices[0].message.content.strip().lower()
            similarity = "true" in content
            
            results.append({
                "ground_truth": item["ground_truth"],
                "prediction": item["prediction"],
                "similarity": similarity,
                "similarity_score": 1.0 if similarity else 0.0 
            })
            
            time.sleep(0.3)
        
        except Exception as e:
            results.append({
                "ground_truth": item.get("ground_truth", ""),
                "prediction": item.get("prediction", ""),
                "similarity": False,
                "similarity_score": 0.0,
                "error": str(e)
            })
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
    
    similar_count = sum(1 for r in results if r.get('similarity', False))
    return total, similar_count

def main(args):

    total, similar = process_file(args.input_file, args.output_file)
    
    print(f"Processed {total} entries")
    print(f"Similar entries: {similar} ({similar/total:.2%})")
    print(f"Results saved to: {args.output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Function name correctness evaluation based on ChatGPT')
    parser.add_argument('-i', '--input_file', type=str, required=True,
                        help='Input file containing predicted function names and ground truth.')
    parser.add_argument('-o', '--output_file', type=str, required=True,
                        help='Output path for processed results')
    args = parser.parse_args()

    main(args)

