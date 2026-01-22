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

class CotDataConstruction:
    """Class for constructing conversational data from decompiled code."""

    @classmethod
    def build_summary_conversation(cls, func_name: str, metadata: dict):
        """Build summary conversation for function analysis."""
        conversation = []
        
        if 'history' in metadata and len(metadata['history']) > 0:
            for name, item in metadata['history'].items():
                user_input = f"""# You are a helpful, respectful and honest assistant proficient in understanding programming languages. Your task is to generate detailed natural language descriptions for given code snippet.

## Input You Will Receive: A section of C language code.

## Your Tasks:
Generate Descriptions for **Each Input Code Snippet**:
1. Describe what the code does from a functional perspective, highlighting the main steps and the main purpose of the code.
2. Avoid unnecessary details and focus on delivering a high-level overview.

## Guidelines for Generating Descriptions:
1. Provide Detailed Explanations:
 - Ensure that each description thoroughly explains the code's functionality and mechanisms.
 - Limit each description to one or two sentences.
2. Use Direct and Engaging Language:
 - Start the first sentence with verbs.
 - Do not mention specific function names which start with 'FUN_'.

Input Data are as following:
Block Code:
```C \n{item['input']}\n```

"""

                assistant_output = f"{item['output']}"
                
                conversation.append({
                    "messages": [
                        {"role": "user", "content": user_input},
                        {"role": "assistant", "content": assistant_output}
                    ]
                })
        
        func_code = metadata['input']
        func_code = remove_all_comments(func_code)
        decompiled_lines = func_code.split('\n')
        non_empty_lines = [line for line in decompiled_lines if line.strip()]
        
        if len(non_empty_lines) > 5:
            final_code = metadata['input'].replace(func_name, '[MASK]', 1)
            final_summary = metadata['output']

            func_input = f"""# You are a helpful, respectful and honest assistant proficient in understanding programming languages. Your task is to generate detailed natural language descriptions for given function code.

## Input You Will Receive: A section of C language code.

## Your Tasks:
Generate Descriptions for **Each Input Code Snippet**:
1. Describe what the code does from a functional perspective, highlighting the key inputs, outputs, main steps and the main purpose of the function.
2. Describe the structural features that remain consistent after compilation.
3. Avoid unnecessary details and focus on delivering a high-level overview.

## Guidelines for Generating Descriptions:
1. Provide Detailed Explanations:
 - Ensure that each description thoroughly explains the code's functionality and mechanisms.
 - Limit each description to a single paragraph of no more than 512 words.
2. Use Direct and Engaging Language:
 - Start the first sentence with verbs.
 - Do not mention specific function names which start with 'FUN_'.

Input Data are as following:
Function Code:
```C\n{final_code}\n```
"""

            func_output = f"{final_summary}"

            conversation.append({
                "messages": [
                    {"role": "user", "content": func_input},
                    {"role": "assistant", "content": func_output}
                ]
            })
        
        return conversation

    @classmethod
    def build_train_conversation(cls, func_name: str, metadata: dict):

        conversation = []
        
        func_code = metadata['input']
        func_code = remove_all_comments(func_code)
        decompiled_lines = func_code.split('\n')
        non_empty_lines = [line for line in decompiled_lines if line.strip()]
        
        if len(non_empty_lines) > 3:
            bin_info = metadata['bin_info']

            callee_internal_list = []

            for idx, callee_ in enumerate(metadata['callee_internal']):
                if not isinstance(callee_, str):
                    for callee_name, callee_summary in callee_.items():
                        callee_internal_list.append(f'callee {callee_name}: {callee_summary}')
                if len(callee_internal_list) > 4:
                    break

            callee_internal = '\n'.join(callee_internal_list)

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

            suggested_name = func_name.lower()
            final_output = f"The function name is: {suggested_name}."

            conversation.append({
                "messages": [
                    {"role": "user", "content": final_input},
                    {"role": "assistant", "content": final_output}
                ]
            })

        return conversation
    
    @classmethod
    def build_test_conversation(cls, func_name: str, metadata: dict):
        conversation = []
        
        func_code = metadata['input']
        func_code = remove_all_comments(func_code)
        decompiled_lines = func_code.split('\n')
        non_empty_lines = [line for line in decompiled_lines if line.strip()]

        
        if len(non_empty_lines) > 3:
            bin_info = metadata['bin_info']

            callee_internal_list = []

            for idx, callee_ in enumerate(metadata['callee_internal']):
                if not isinstance(callee_, str):
                    for callee_name, callee_summary in callee_.items():
                        try:

                            callee_internal_list.append(f'callee {callee_name}: {callee_summary}')
                        except:
                            print(callee_name)
                if len(callee_internal_list) > 4:
                    break

            callee_internal = '\n'.join(callee_internal_list)

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
            suggested_name = func_name.lower()
            final_output = (
                f"The function name is: {suggested_name}."
            )

            conversation.append([
                {"role": "user", "content": final_input},
                {"role": "assistant", "content": final_output}
            ])

        return conversation


    @classmethod
    def build_valid_conversation(cls, func_name: str, metadata: dict):

        conversation = []

        func_code = metadata['input']
        func_code = remove_all_comments(func_code)
        decompiled_lines = func_code.split('\n')
        non_empty_lines = [line for line in decompiled_lines if line.strip()]

        if len(non_empty_lines) > 3:
            bin_info = metadata['bin_info']

            callee_internal_list = []

            for idx, callee_ in enumerate(metadata['callee_internal']):
                if not isinstance(callee_, str):
                    for callee_name, callee_summary in callee_.items():
                        try:
                            callee_internal_list.append(f'callee {callee_name}: {callee_summary}')
                        except:
                            print(callee_name)
                if len(callee_internal_list) > 4:
                    break

            callee_internal = '\n'.join(callee_internal_list)

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

            suggested_name = func_name.lower()
            final_output = (
                f"The function name is: {suggested_name}."
            )

            conversation.append({
                "messages": [
                    {"role": "user", "content": final_input},
                    {"role": "assistant", "content": final_output}
                ]
            })

        return conversation


    @classmethod
    def prepare_all_training_dataset(cls, train_data_base: str, summary_path: str, output_dir: str, arch: str, opt_list: list):
        """Prepare training dataset from multiple sources."""

        train_data_list = []

        for opt in opt_list:
            training_data_path = os.path.join(train_data_base, f'{arch}/{opt}/training_set_with_call.json')
            try:
                with open(training_data_path, 'r') as f:
                    train_data = json.load(f)
                    train_data_list.append(train_data)
            except (FileNotFoundError, json.JSONDecodeError):
                continue

        summary_data_list = []

        for opt in opt_list:
            summary_data_path = os.path.join(summary_path, f'{arch}/{opt}/function_summary_decompiled.json')
            try:
                with open(summary_data_path, 'r') as f:
                    summary_data = json.load(f)
                    summary_data_list.append(summary_data)
            except (FileNotFoundError, json.JSONDecodeError):
                continue


        decompiled_samples = []
        
        for summary_data in summary_data_list:
            for item in summary_data:
                for func_name, metadata in item.items():
                    if func_name in SKIP_FUNCTIONS:
                        continue
                    if 'input' in metadata:
                        conversation = cls.build_summary_conversation(func_name, metadata)
                        if len(conversation)>0:
                            decompiled_samples.extend(conversation)
        
        for train_data in train_data_list:
            for data_item in train_data:
                for func_name, metadata in data_item.items():
                    if func_name in SKIP_FUNCTIONS:
                        continue
                    conversation = cls.build_train_conversation(func_name, metadata)
                    if len(conversation)>0:
                        decompiled_samples.extend(conversation)

        os.makedirs(output_dir, exist_ok=True)
        
        output_file = os.path.join(output_dir, f"training_rename_{arch}_dataset.json")
        with open(output_file, 'w') as f:
            json.dump(decompiled_samples, f, indent=4)
        print(f"[+] Save merged training set to {output_file}")

    @classmethod
    def prepare_all_valid_dataset(cls, valid_data_base: str, output_dir: str, arch: str, opt_list: list):
        """Prepare validation dataset."""
        valid_data_list = []

        for opt in opt_list:
            validation_data_path = os.path.join(valid_data_base, f'{arch}/{opt}/validation_set_with_call.json')
            try:
                with open(validation_data_path, 'r') as f:
                    validation_data = json.load(f)
                    valid_data_list.append(validation_data)
            except (FileNotFoundError, json.JSONDecodeError):
                continue


        decompiled_samples = []
        
        for valid_data in valid_data_list:
            for data_item in valid_data:
                for func_name, metadata in data_item.items():
                    if func_name in SKIP_FUNCTIONS:
                        continue
                    conversation = cls.build_valid_conversation(func_name, metadata)
                    if len(conversation) > 0:
                        decompiled_samples.extend(conversation)

        os.makedirs(output_dir, exist_ok=True)

        output_file = os.path.join(output_dir, f"validation_{arch}_dataset.json")
        with open(output_file, 'w') as f:
            json.dump(decompiled_samples, f, indent=4)
        print(f"[+] Save merged validation set to {output_file}")

    @classmethod
    def prepare_test_conversational_dataset(cls, test_data_path: str, output_dir: str, arch: str, opt: str):
        """Prepare test conversational dataset."""
        
        try:
            test_data_path = os.path.join(test_data_path, f'{arch}/{opt}/test_set_with_call.json')
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
        output_file = os.path.join(output_dir, f"test_{arch}_{opt}_dataset.json")
        with open(output_file, 'w') as f:
            json.dump(samples, f, ensure_ascii=False, indent=2)
        print(f"[+] Save test dataset to {output_file}")

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Prepare training, validation and test datasets for decompiled code analysis.')
    parser.add_argument('-d', '--data_path', type=str, required=True,
                        help='Directory to the JSON file containing the decompiled function and its calling context.')
    parser.add_argument('-s', '--summary_data', type=str, default='',
                        help='Directory to the directory containing training function summaries.')
    parser.add_argument('-p', '--prediction', action='store_true',
                        help='Flag to indicate preparation of test set for prediction.')
    parser.add_argument('-o', '--output_dir', type=str, default='',
                        help='Directory to save the output files.')
    parser.add_argument('--arch', type=str, default='',
                        help='Architecture type for the input dataset.')
    parser.add_argument('--opt_list', nargs='+', default=['O0', 'O1', 'O2', 'O3'],
                        help='List of compilation optimization levels (default: O0 O1 O2 O3).')
    parser.add_argument('--opt', type=str, default='',
                        help='Specific optimization level for test dataset.')

    args = parser.parse_args()

    if len(args.summary_data) > 0 and not args.prediction:
        CotDataConstruction.prepare_all_training_dataset(
            train_data_base=args.data_path,
            summary_path=args.summary_data,
            output_dir=args.output_dir,
            arch=args.arch,
            opt_list=args.opt_list
        )

    # Prepare validation dataset
    if len(args.summary_data) == 0 and not args.prediction:
        CotDataConstruction.prepare_all_valid_dataset(
            valid_data_base=args.data_path,
            output_dir=args.output_dir,
            arch=args.arch,
            opt_list=args.opt_list
        )

    # Prepare test dataset if prediction flag is set
    if args.prediction:
        CotDataConstruction.prepare_test_conversational_dataset(
            test_data_path=args.data_path,
            output_dir=args.output_dir,
            arch=args.arch,
            opt=args.opt
        )