import sys
import json
import os
import random
import argparse
import multiprocessing
from multiprocessing import Pool, Manager
import traceback
import tempfile
import uuid
from collections import defaultdict

from antlr4 import *
from antlr.CLexer import CLexer
from antlr.CParser import CParser
from antlr.CVisitor import CVisitor

SKIPPED_FUNCTIONS = [
    'deregister_tm_clones', 'register_tm_clones', '__do_global_dtors_aux', '_start',
    '__x86.get_pc_thunk.bx', 'frame_dummy', '__x86.get_pc_thunk.dx',
    '__x86.get_pc_thunk.ax', '__libc_csu_init', '__libc_csu_fini', 'main', '_FINI_0', '_DT_INIT'
]


class SubstituteFunctionNameVisitor(CVisitor):
    def __init__(self, lines, column_offset, function_map, function_count_ref):
        self.lines = lines
        self.column_offset = column_offset
        self.function_map = function_map
        self.function_count_ref = function_count_ref

    def visitPostfixExpression(self, ctx: CParser.PostfixExpressionContext):
        if ctx.LeftParen() is not None and ctx.primaryExpression() is not None:
            if ctx.primaryExpression().Identifier() is not None:
                function_token = ctx.primaryExpression().Identifier().getSymbol()
                if function_token.text.startswith('sub_'):
                    if function_token.text in self.function_map:
                        new_function_name = self.function_map[function_token.text]
                    else:
                        with self.function_count_ref.get_lock():
                            new_function_name = 'sub_' + str(self.function_count_ref.value)
                            self.function_map[function_token.text] = new_function_name
                            self.function_count_ref.value += 1

                    line_idx = function_token.line - 1
                    col_offset = self.column_offset.get(line_idx, 0)

                    old_line = self.lines[line_idx]
                    new_line = (old_line[:function_token.column + col_offset] +
                                new_function_name +
                                old_line[function_token.column + len(function_token.text) + col_offset:])
                    self.lines[line_idx] = new_line

                    self.column_offset[line_idx] = col_offset + len(new_function_name) - len(function_token.text)
        return self.visitChildren(ctx)


def substitute_decompiled(code, process_id=None):
    temp_file = None
    try:
        if process_id is None:
            process_id = multiprocessing.current_process().pid

        unique_id = str(uuid.uuid4())[:8]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                         prefix=f'code_{process_id}_{unique_id}_') as tmp:
            temp_file = tmp.name
            tmp.write(code)

        with open(temp_file, 'r') as f:
            code_content = f.read()
            antlr_input = InputStream(code_content)

        with open(temp_file, 'r') as f:
            lines = f.readlines()

        column_offset = {}
        for i in range(len(lines)):
            column_offset[i] = 0

        function_map = {}
        function_count_ref = multiprocessing.Value('i', 0)

        lexer = CLexer(antlr_input)
        stream = CommonTokenStream(lexer)
        parser = CParser(stream)
        tree = parser.compilationUnit()

        visitor = SubstituteFunctionNameVisitor(lines, column_offset, function_map, function_count_ref)
        visitor.visit(tree)

        result = "".join(lines)
        return result
    except Exception as e:
        print(f"Error processing code (PID: {process_id}): {e}")
        return None
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass


def process_binary_file(args):
    binary_filename, file_path, binary_name, binary_info, dataset_type, shared_data, output_queue = args

    try:
        process_id = multiprocessing.current_process().pid
        print(f'[PID:{process_id}] Processing {binary_filename} for {dataset_type} set')
        if dataset_type == 'test':
            samples = []
        else:
            samples = {}

        with open(file_path) as f:
            json_data = json.load(f)

        for function_name in json_data.keys():
            if 'sub_' in function_name:
                continue

            if function_name in SKIPPED_FUNCTIONS:
                continue

            with shared_data['existed_function_names_lock']:
                if function_name in shared_data['existed_function_names']:
                    continue
                shared_data['existed_function_names'].append(function_name)

            if dataset_type == 'train':
                decompiled_code = json_data[function_name]['unstripped']['code']
            else:
                decompiled_code = json_data[function_name]['stripped']['code']

            if not decompiled_code:
                continue

            try:
                modified_decompiled_code = substitute_decompiled(json_data[function_name]['stripped']['code'], process_id)
            except:
                modified_decompiled_code = json_data[function_name]['stripped']['code']

            with shared_data['existed_function_bodies_lock']:
                if modified_decompiled_code in shared_data['existed_function_bodies']:
                    continue
                shared_data['existed_function_bodies'].append(modified_decompiled_code)

            sample = defaultdict(dict)
            sample[function_name]['bin_name'] = binary_name
            sample[function_name]['bin_info'] = binary_info
            sample[function_name]["output"] = 'The predicted function name is: ' + function_name + '. '

            if dataset_type == 'test':
                sample[function_name]['stripped'] = decompiled_code
                sample[function_name]['callee_internal'] = json_data[function_name]['stripped']['callee_internal']
                sample[function_name]['callee_external'] = json_data[function_name]['stripped']['callee_external']
                sample[function_name]['caller'] = json_data[function_name]['stripped']['caller']
                samples.append(sample)

            elif dataset_type == 'train':
                sample[function_name]['unstripped'] = json_data[function_name]['unstripped']['code']
                sample[function_name]['stripped'] = json_data[function_name]['stripped']['code']
                sample[function_name]['callee_internal'] = json_data[function_name]['stripped']['callee_internal']
                sample[function_name]['callee_external'] = json_data[function_name]['stripped']['callee_external']
                sample[function_name]['caller'] = json_data[function_name]['stripped']['caller']
                samples.update(sample)
            else:
                sample[function_name]['stripped'] = decompiled_code
                sample[function_name]['callee_internal'] = json_data[function_name]['stripped']['callee_internal']
                sample[function_name]['callee_external'] = json_data[function_name]['stripped']['callee_external']
                sample[function_name]['caller'] = json_data[function_name]['stripped']['caller']
                samples.update(sample)

        if samples:
            output_queue.put((dataset_type, samples))

        print(f'[PID:{process_id}] Finished processing {binary_filename}, generated {len(samples)} samples')
        return len(samples)

    except Exception as e:
        print(f"Error processing {binary_filename} (PID:{process_id}): {e}")
        traceback.print_exc()
        return 0


def process_dataset_parallel(binary_context_path, input_dir, output_dir, max_workers=None):
    ### Divide Binary
    train_part = 0.8
    test_part = 0.1
    validation_part = 0.1


    binary_filenames = []
    file_path_map = {}
    binary_info_map = {}
    for root, dirs, files in os.walk(input_dir):
        for file in files:
            binary_filenames.append(file)
            file_path_map[file] = os.path.join(root, file)
            binary_path = os.path.join(root, file)
            bin_name = binary_path.split('/')[-2:]
            bin_name = '/'.join(bin_name)
            binary_info_map[file] = bin_name.replace('.json', '')

    random.shuffle(binary_filenames)

    train_binaries = binary_filenames[0: int(train_part * len(binary_filenames))]
    test_binaries = binary_filenames[int(train_part * len(binary_filenames)): int((train_part + test_part) * len(binary_filenames))]
    validation_binaries = binary_filenames[int((train_part + test_part) * len(binary_filenames)):]

    with open(binary_context_path, 'r') as f:
        binary_datasets = json.load(f)

    print(f"[+] Training Set: {len(train_binaries)} files")
    print(f"[+] Test Set: {len(test_binaries)} files")
    print(f"[+] Validation Set: {len(validation_binaries)} files")

    manager = Manager()
    shared_data = {
        'existed_function_names': manager.list(),
        'existed_function_bodies': manager.list(),
        'existed_function_names_lock': manager.Lock(),
        'existed_function_bodies_lock': manager.Lock()
    }

    output_queue = manager.Queue()

    process_args = []

    for binary in train_binaries:
        try:
            binary_name = binary_info_map[binary]
            binary_info = binary_datasets[binary_name]
            process_args.append((binary, file_path_map[binary], binary_name, binary_info, 'train', shared_data, output_queue))
        except:
            print(f"error!!!!!! {binary}")

    for binary in test_binaries:
        try:
            binary_name = binary_info_map[binary]
            binary_info = binary_datasets[binary_name]
            process_args.append((binary, file_path_map[binary], binary_name, binary_info, 'test', shared_data, output_queue))
        except:
            print(f"error!!!!!! {binary}")

    for binary in validation_binaries:
        try:
            binary_name = binary_info_map[binary]
            binary_info = binary_datasets[binary_name]
            process_args.append((binary, file_path_map[binary], binary_name, binary_info, 'validation', shared_data, output_queue))
        except:
            print(f"error!!!!!! {binary}")

    print(f"[+] Total files to process: {len(process_args)}")

    if max_workers is None:
        max_workers = min(multiprocessing.cpu_count(), len(process_args))

    print(f"[+] Using {max_workers} processes")

    train_samples = {}
    test_samples = []
    validation_samples = {}

    with Pool(processes=max_workers) as pool:
        try:
            results = pool.imap_unordered(process_binary_file, process_args, chunksize=1)

            completed_count = 0
            for result in results:
                completed_count += 1
                if completed_count % 10 == 0:
                    print(f"[+] Processed {completed_count}/{len(process_args)} files")

            print("[+] Collecting results from queue...")
            while not output_queue.empty():
                dataset_type, samples = output_queue.get()
                if dataset_type == 'train':
                    train_samples.update(samples)
                elif dataset_type == 'test':
                    test_samples.extend(samples)
                elif dataset_type == 'validation':
                    validation_samples.update(samples)

        except KeyboardInterrupt:
            print("\n[!] Interrupted by user")
            pool.terminate()
            pool.join()
            sys.exit(1)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    print(
        f"[+] Saving results: train={len(train_samples)}, test={len(test_samples)}, validation={len(validation_samples)}")

    with open(os.path.join(output_dir, 'training_set.json'), 'w') as f:
        json.dump(train_samples, f, indent=4)
        print("[+] Save training set to", os.path.join(output_dir, 'training_set.json'))

    with open(os.path.join(output_dir, 'test_set.json'), 'w') as f:
        json.dump(test_samples, f, indent=4)
        print("[+] Save test set to", os.path.join(output_dir, 'test_set.json'))

    with open(os.path.join(output_dir, 'validation_set.json'), 'w') as f:
        json.dump(validation_samples, f, indent=4)
        print("[+] Save validation set to", os.path.join(output_dir, 'validation_set.json'))


def main(args):
    binary_context_path = args.binary_context_path
    input_dir = args.input_dir
    output_dir = args.output_dir
    max_workers = args.max_workers if hasattr(args, 'max_workers') else None

    process_dataset_parallel(binary_context_path, input_dir, output_dir, max_workers)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Divide data into training, test and validation set.')
    parser.add_argument('-b', '--binary_context_path', type=str, required=True,
                        help='Path containing global binary context from stripped binaries.')
    parser.add_argument('-i', '--input_dir', type=str, required=True,
                        help='Directory containing the combined decompiled code.')
    parser.add_argument('-o', '--output_dir', type=str, required=True,
                        help='Directory to save the divided dataset.')
    parser.add_argument('--max_workers', type=int, default=32,
                        help='Maximum number of worker processes (default: 32).')
    args = parser.parse_args()
    main(args)