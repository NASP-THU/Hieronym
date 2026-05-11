import sys
import json
import os
import argparse
import multiprocessing
from multiprocessing import Pool, Manager
import traceback
from collections import defaultdict

SKIPPED_FUNCTIONS = [
    'deregister_tm_clones', 'register_tm_clones', '__do_global_dtors_aux', '_start',
    '__x86.get_pc_thunk.bx', 'frame_dummy', '__x86.get_pc_thunk.dx',
    '__x86.get_pc_thunk.ax', '__libc_csu_init', '__libc_csu_fini', 'main', '_FINI_0', '_DT_INIT'
]



def process_binary_file(args):
    file_path, binary_info, output_queue = args

    try:
        process_id = multiprocessing.current_process().pid
        print(f'[PID:{process_id}] Processing')
       
        samples = []
        

        with open(file_path) as f:
            json_data = json.load(f)

        for function_name, func_body in json_data.items():
            if 'FUN_' in function_name:
                continue

            if function_name in SKIPPED_FUNCTIONS:
                continue

            decompiled_code = func_body['code']

            if not decompiled_code:
                continue

            
            sample = defaultdict(dict)
            sample[function_name]['bin_info'] = binary_info
            sample[function_name]["output"] = 'The predicted function name is: ' + func_body['stripped_function_name'] + '. '

            
            sample[function_name]['stripped'] = decompiled_code
            sample[function_name]['callee_internal'] = func_body.get('callee_internal', [])
            sample[function_name]['callee_external'] = func_body.get('callee_external',[])
            sample[function_name]['caller'] = func_body.get('caller',[])
            samples.append(sample)

            

        if samples:
            output_queue.put((samples))

        print(f'[PID:{process_id}] Finished, generated {len(samples)} samples')
        return len(samples)

    except Exception as e:
        print(f"Error processing (PID:{process_id}): {e}")
        traceback.print_exc()
        return 0


def process_dataset_parallel(binary_information_path, input_file, output_dir, max_workers=None):
    
    with open(binary_information_path, 'r') as f:
        binary_datasets = json.load(f)
    
    manager = Manager()

    output_queue = manager.Queue()
    process_args =[]
    try:
        binary_info = list(binary_datasets.values())[0]
        process_args.append((input_file, binary_info, output_queue))
    except Exception as e:
        print(f"error!!!!!! {e}")


    test_samples = []
   
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
                samples = output_queue.get()
                test_samples.extend(samples)
                

        except KeyboardInterrupt:
            print("\n[!] Interrupted by user")
            pool.terminate()
            pool.join()
            sys.exit(1)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    print(
        f"[+] Saving results: test={len(test_samples)}")


    with open(os.path.join(output_dir, 'merge_data_with_bin.json'), 'w') as f:
        json.dump(test_samples, f, indent=4)
        print("[+] Save test set to", os.path.join(output_dir, 'test_set.json'))



def main(args):
    binary_context_path = args.binary_context_path
    input_file = args.input_file
    output_dir = args.output_dir
    max_workers = args.max_workers if hasattr(args, 'max_workers') else None

    process_dataset_parallel(binary_context_path, input_file, output_dir, max_workers)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Prepare test set.')
    parser.add_argument('-b', '--binary_context_path', type=str, required=True,
                        help='Path containing global binary context from stripped binaries.')
    parser.add_argument('-i', '--input_file', type=str, required=True,
                        help='Path containing the stripped decompiled code.')
    parser.add_argument('-o', '--output_dir', type=str, required=True,
                        help='Directory to save the test set.')
    parser.add_argument('--max_workers', type=int, default=32,
                        help='Maximum number of worker processes (default: 32).')
    args = parser.parse_args()
    main(args)
