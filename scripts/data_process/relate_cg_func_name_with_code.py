import json
import multiprocessing
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Tuple, Any, Optional
import time
import os
import argparse
import sys

skip_functions = ['deregister_tm_clones', 'register_tm_clones', 'do_global_dtors_aux', 'frame_dummy',
                  'x86.get_pc_thunk.dx', '_x86.get_pc_thunk.ax', '_x86.get_pc_thunk.bx', '__libc_csu_init',
                  '__libc_csu_fini', 'main']


class FunctionInlineReplacer:
    def __init__(self, functions_data: Dict[str, Dict]):
        self.functions_data = functions_data

    def inline_function_calls(self, target_func_name: str) -> Dict[str, Any]:
        if target_func_name not in self.functions_data:
            raise KeyError(f"Function {target_func_name} not found in functions_data")

        func_data = self.functions_data[target_func_name].copy()

        for data_type in ['unstripped', 'stripped']:
            if data_type not in func_data:
                continue

            callees_to_inline = []
            callers_to_inline = []

            if "callee_internal" in func_data[data_type]:
                callees = func_data[data_type]["callee_internal"]
                if isinstance(callees, list):
                    callees_to_inline.extend(callees)

            if "caller" in func_data[data_type]:
                callers = func_data[data_type]["caller"]
                if isinstance(callers, list):
                    callers_to_inline.extend(callers)

            inlined_callee_codes = []
            for callee_name in callees_to_inline:
                if callee_name in skip_functions:
                    continue
                if callee_name not in self.functions_data:
                    #print(f"Warning: Callee function {callee_name} not found in functions_data")
                    continue
                callee_data = self.functions_data[callee_name]
                if data_type in callee_data:
                    callee_code = callee_data[data_type]['code']
                    if data_type == 'stripped':
                        stripped_callee_name = self.functions_data[callee_name]['stripped_function_name']
                        inlined_callee_codes.append({stripped_callee_name:callee_code})
                    else:
                        inlined_callee_codes.append({callee_name: callee_code})

            inlined_caller_code = []
            for caller_name in callers_to_inline:
                if caller_name in skip_functions:
                    continue
                if caller_name not in self.functions_data:
                    #print(f"Warning: Caller function {caller_name} not found for {target_func_name}")
                    continue
                caller_data = self.functions_data[caller_name]
                if data_type in caller_data:
                    caller_code = caller_data[data_type]['code']
                    if data_type == 'stripped':
                        stripped_caller_name = self.functions_data[caller_name]['stripped_function_name']
                        inlined_caller_code.append({stripped_caller_name: caller_code})
                    else:
                        inlined_caller_code.append({caller_name: caller_code})

            if inlined_callee_codes:
                func_data[data_type]["callee_internal"] = inlined_callee_codes
            if inlined_caller_code:
                func_data[data_type]["caller"] = inlined_caller_code

        return func_data

    def process_all_functions(self) -> Dict[str, Dict]:
        results = {}
        for func_name in self.functions_data:
            if func_name in skip_functions:
                continue
            if "FUN_" in func_name:
                continue
            try:
                inlined_code = self.inline_function_calls(func_name)
                results[func_name] = inlined_code
            except Exception as e:
                print(f"Error processing {func_name}: {e}")
                results[func_name] = self.functions_data[func_name]

        return results


def load_json_file(file_path: str) -> Dict[str, Any]:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"JSON decode error in {file_path}: {e}")
        return {}
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return {}


def process_single_file(file_info: Tuple[str, str]) -> Tuple[str, Dict[str, Any]]:
    # Load data
    file_path, output_directory = file_info
    try:
        functions_data = load_json_file(file_path)
        if not functions_data:
            return file_path, {}

        # Process functions
        replacer = FunctionInlineReplacer(functions_data)
        results = replacer.process_all_functions()

        # Save results
        binary_name = os.path.basename(file_path)
        try:
            if not os.path.exists(output_directory):
                os.makedirs(output_directory)
        except:
            pass

        output_file = os.path.join(output_directory, binary_name)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        return file_path, results

    except Exception as e:
        print(f"Error processing {file_path}: {e}")


def find_json_files(input_dir: str) -> List[str]:
    input_path = Path(input_dir)
    if not input_path.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    if input_path.is_file() and input_path.suffix.lower() == '.json':
        return [str(input_path)]

    json_files = list(input_path.rglob("*.json"))
    return [str(f) for f in json_files if f.is_file()]


def process_files_multiprocess(input_dir: str, output_dir: str, max_workers: Optional[int] = None):
    # Find JSON files
    json_files = find_json_files(input_dir)
    if not json_files:
        print(f"No JSON files found in {input_dir}")
        return {}
    print(f"Found {len(json_files)} JSON files to process")

    # Determine number of processes
    if max_workers is None:
        max_workers = min(multiprocessing.cpu_count(), len(json_files))
    else:
        max_workers = max(1, min(max_workers, len(json_files)))
    print(f"Using {max_workers} processes")
    print("=" * 60)

    # Prepare output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Prepare file information list
    file_infos = [(file_path, output_dir) for file_path in json_files]
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {executor.submit(process_single_file, file_info): file_info[0] for file_info in file_infos}

        completed = 0
        for future in as_completed(future_to_file):
            file_path = future_to_file[future]
            completed += 1

            try:
                result_file_path, results = future.result()
                print(f"[{completed}/{len(json_files)}] Processed: {file_path}")
            except Exception as e:
                print(f"[{completed}/{len(json_files)}] Failed: {file_path} - {e}")


def main(config):

    if not config.verbose:
        import warnings
        warnings.filterwarnings('ignore')

    print(f"[+] Input: {config.input}")
    print(f"[+] Output directory: {config.output}")
    print(f"[+] Worker processes: {config.workers or 'auto'}")
    print("=" * 60)

    try:
        process_files_multiprocess(input_dir=config.input, output_dir=config.output, max_workers=config.workers)
    except KeyboardInterrupt:
        print("\nProcess interrupted by user")
    except Exception as e:
        print(f"Error during processing: {e}")
        sys.exit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Connect the calling function with its code')
    parser.add_argument('-i', '--input', required=True,
                        help='Input directory containing JSON files or a single JSON file')
    parser.add_argument('-o', '--output', default='', help='Output directory')
    parser.add_argument('-w', '--workers', type=int, default=32, help='Number of worker processes (default: 32)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')

    args = parser.parse_args()
    main(args)