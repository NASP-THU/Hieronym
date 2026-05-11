import os
import json
import argparse
import re

# Skip list for common compiler-generated functions
SKIP_FUNCTIONS = [
    'deregister_tm_clones', 'register_tm_clones', '__do_global_dtors_aux', 
    '_start', '__x86.get_pc_thunk.bx', 'frame_dummy', '__x86.get_pc_thunk.dx', 
    '__x86.get_pc_thunk.ax', '__libc_csu_init', '__libc_csu_fini', 'main'
]

def remove_all_comments(code):
    """Remove all types of comments from code."""
    pattern = r'''
        ("(?:\\.|[^"\\])*")        # Match string literals
        |(/\*.*?\*/)               # Match /* */ comments
        |(//.*?$)                  # Match // comments
    '''
    return re.sub(pattern, lambda m: m.group(1) if m.group(1) else '',
                  code, flags=re.DOTALL | re.MULTILINE | re.VERBOSE)

class DataConstruction:
    """Class for constructing conversational data from decompiled code."""

    @classmethod
    def build_test_conversation(cls, func_name: str, metadata: dict):
        conversation = []
        
        func_code = metadata['input']
        func_code = remove_all_comments(func_code)
        decompiled_lines = func_code.split('\n')
        non_empty_lines = [line for line in decompiled_lines if line.strip()]

        
        if len(non_empty_lines) > 8:
            bin_info = metadata['bin_info']

            internal_callees = []

            for idx, callee_ in enumerate(metadata['callee_internal']):
                if not isinstance(callee_, str):
                    for callee_name, callee_summary in callee_.items():
                        try:

                            internal_callees.append(f'callee {callee_name}: {callee_summary}')
                        except:
                            print(callee_name)
                if len(internal_callees) > 4:
                    break

            callee_internal = '\n'.join(internal_callees)

            callee_external = ', '.join(metadata['callee_external'])

            caller_list = []
            for idx, caller_ in enumerate(metadata['caller']):
                if not isinstance(caller_, str):
                    for caller_name, caller_summary in caller_.items():
                        caller_list.append(f'caller {idx + 1}: {caller_summary}')
                if len(caller_list) > 4:
                    break

            caller = '\n'.join(caller_list)

            final_code = func_code

            final_input = f"""# You are a seasoned reverse engineering analyst specializing in reconstructing original symbols from decompiled binaries.

## Your Tasks:
Analyze the provided information, you should infer code semantics and tell me the original function name from the contents of the function to replace [MASK].

##Output: Provide only the function name. No explanations.

## Input:
1. Environmental Context:
{bin_info}

2. Call Context:
2.1. names of external callees: {callee_external}

2.2. summaries of partial internal callees:
{callee_internal}

2.3. summaries of partial callers:
{caller}

3. Decompiled Code:
```C\n{final_code}\n```
"""
    
            final_output =  metadata['target']

            conversation.append([
                {"role": "user", "content": final_input},
                {"role": "assistant", "content": final_output}
            ])

        return conversation

    @classmethod
    def prepare_test_conversational_dataset(cls, test_data_path: str, output_dir: str):
        """Prepare test conversational dataset."""
        
        try:
            with open(test_data_path) as f:
                raw_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
    
        samples = []
        for item in raw_data:
            for func_name, metadata in item.items():
                if func_name in SKIP_FUNCTIONS:
                    continue
                if 'input' in metadata:
                    conversation = cls.build_test_conversation(func_name, metadata)
                    if len(conversation)>0:
                        samples.extend(conversation)

        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"test_dataset.json")
        with open(output_file, 'w') as f:
            json.dump(samples, f, ensure_ascii=False, indent=2)
        print(f"[+] Save test dataset to {output_file}")

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Prepare test datasets for function renaming.')
    parser.add_argument('-d', '--data_path', type=str, required=True,
                        help='Directory to the JSON file containing the decompiled function and its calling context.')
    parser.add_argument('-o', '--output_dir', type=str, default='',
                        help='Directory to save the output files.')

    args = parser.parse_args()

  
    DataConstruction.prepare_test_conversational_dataset(
        test_data_path=args.data_path,
        output_dir=args.output_dir,
    )