import os
import sys
import re
import json
import argparse
import tempfile
import traceback
from collections import defaultdict
from multiprocessing import Pool
from antlr4 import *
from antlr.CLexer import CLexer
from antlr.CParser import CParser
from antlr.CVisitor import CVisitor

sys.setrecursionlimit(100000)


class MaskFunctionNameVisitor(CVisitor):
    def __init__(self, lines):
        self.lines = lines

    def visitFunctionDefinition(self, ctx: CParser.FunctionDefinitionContext):
        if ctx.declarator() is not None:
            if ctx.declarator().directDeclarator() is not None:
                if ctx.declarator().directDeclarator().directDeclarator() is not None:
                    function_name_ctx = ctx.declarator().directDeclarator().directDeclarator()
                    start_token = function_name_ctx.start
                    stop_token = function_name_ctx.stop
                    self.lines[start_token.line - 1] = self.lines[start_token.line - 1][
                                                       0:start_token.column] + '[MASK]' + self.lines[
                                                                                              start_token.line - 1][
                                                                                          stop_token.column + len(
                                                                                              stop_token.text):]
        return self.visitChildren(ctx)


def mask_function_in_code(code, process_id=None):
    temp_filepath = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, prefix='code_') as tmp:
            temp_filepath = tmp.name
            tmp.write(code)

        with open(temp_filepath, 'r') as f:
            antlr_input = InputStream(f.read())
        with open(temp_filepath, 'r') as f:
            lines = f.readlines()

        lexer = CLexer(antlr_input)
        stream = CommonTokenStream(lexer)
        parser = CParser(stream)
        tree = parser.compilationUnit()
        visitor = MaskFunctionNameVisitor(lines)
        visitor.visit(tree)
        result = ''.join(lines)
        return result
    except Exception as e:
        print(f"Error processing file {process_id}: {e}")
        return None
    finally:
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except:
                pass


def remove_comments(code):
    code = re.sub(r'//.*$', '', code, flags=re.MULTILINE)
    code = '\n'.join(line for line in code.split('\n') if line.strip())
    return code


def process_single_file_prediction(args):
    root, filename, stripped_path, stripped_call_path, output_dir = args
    try:
        process_id = multiprocessing.current_process().pid
        print(f'[PID:{process_id}] Processing {os.path.join(root, filename)}')

        current_directory = os.path.dirname(os.path.abspath(__file__))
        relative_path = os.path.relpath(root, stripped_path)
        output_directory = os.path.normpath(os.path.join(current_directory, output_dir, relative_path))

        if not os.path.exists(output_directory):
            os.makedirs(output_directory, exist_ok=True)
        if os.path.exists(os.path.join(output_directory, filename)):
            return

        result_dict = defaultdict(dict)
        with open(os.path.join(root, filename)) as f:
            data = json.load(f)

        call_stripped_filepath = os.path.join(stripped_call_path, relative_path, filename)
        with open(call_stripped_filepath) as f:
            call_stripped_data = json.load(f)

        for function_name in data.keys():
            function = data[function_name]
            if function is not None:
                code = function["decomp_code"]

                if function.get("assembly") ==["?? ??"]:
                    continue

                if len(function.get("assembly",[])) > 1024 or len(function.get("assembly",[])) < 10:
                    continue


                stripped_function_call = call_stripped_data.get(function_name)

                masked_code = mask_function_in_code(code, process_id)
                if masked_code:
                    result_dict[function_name]['stripped_function_name'] = function["func_name"]
                    result_dict[function_name]['code'] = remove_comments(masked_code)
                    try:
                        result_dict[function_name]['callee_external'] = stripped_function_call.get('callee_external', [])
                        result_dict[function_name]['callee_internal'] = stripped_function_call.get('callee_internal', [])
                        result_dict[function_name]['caller'] = stripped_function_call.get('caller', [])
                    except:
                        pass

        output_filepath = os.path.join(output_directory, filename)
        with open(output_filepath, 'w') as f:
            print(f'[PID:{process_id}] Write results to {output_filepath}')
            json.dump(result_dict, f, indent=4)
    except Exception as e:
        print(f"Error processing {os.path.join(root, filename)} (PID:{process_id}): {e}")
        traceback.print_exc()


def process_binaries_for_prediction(stripped_path, stripped_call_path, output_dir, num_processes=None):
    file_arguments = []
    for root, dirs, files in os.walk(stripped_path):
        for filename in files:
            file_arguments.append((root, filename, stripped_path, stripped_call_path, output_dir))
    print(f"[+] Found {len(file_arguments)} files to process for prediction")

    if num_processes is None:
        num_processes = min(multiprocessing.cpu_count(), len(file_arguments))
    print(f"[+] Using {num_processes} processes for prediction")

    with Pool(processes=num_processes) as pool:
        try:
            for i, _ in enumerate(pool.imap_unordered(process_single_file_prediction, file_arguments, chunksize=1)):
                if i % 10 == 0:
                    print(f"[+] Processed {i + 1}/{len(file_arguments)} files for prediction")
        except KeyboardInterrupt:
            print("[-] KeyboardInterrupt detected, terminating pool")
            pool.terminate()
            pool.join()
            sys.exit(1)


def main(args):

    stripped_path = args.stripped_bin_path
    stripped_call_path = args.stripped_call_path
    output_dir = args.output_dir
    num_processes = args.max_workers

    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    process_binaries_for_prediction(stripped_path, stripped_call_path, output_dir, num_processes)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Prepare data for prediction.')
    parser.add_argument('-sb', '--stripped_bin_path', type=str, required=True,
                        help="Path to JSON files containing decompiled stripped binaries.")
    parser.add_argument('-sc', '--stripped_call_path', type=str, required=True,
                        help='Path to JSON files containing calling context of stripped binaries.')
    parser.add_argument('-o', '--output_dir', type=str, required=True,
                        help='Directory to save the output files.')
    parser.add_argument('-w', '--max_workers', type=int, default=1,
                        help='Maximum number of worker processes.')
    args = parser.parse_args()

    main(args)