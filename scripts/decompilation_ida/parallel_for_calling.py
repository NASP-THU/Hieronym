import os
from concurrent.futures import ThreadPoolExecutor
import time
import subprocess
import argparse
import sys

thread_num = 10
executor = ThreadPoolExecutor(max_workers=thread_num)


ida_slots = [f'slot_{i}' for i in range(thread_num)]

def is_binary_file(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext not in [".id0", ".id1", ".id2", ".nam", ".til", ".i64", ".idb"]


def run_ida(ida_path, script_path, binary_path):
    """
    IDA headless
    """
    cmd = f'"{ida_path}" -A -S"{script_path}" "{binary_path}"'

    try:
        subprocess.run(cmd, shell=True, timeout=900 * 4)
    except subprocess.TimeoutExpired:
        print(f"[!] timeout for {binary_path}")


def process_unstripped_binary(ida_path, slot_name, binary_path):
    print(f"[*] hold {slot_name} for {binary_path}")

    script = "./decompilation_ida/cg_extractor_for_unstripped.py"
    run_ida(ida_path, script, binary_path)

    ida_slots.append(slot_name)
    print(f"[+] release {slot_name} after finishing {binary_path}")


def process_stripped_binary(ida_path, slot_name, binary_path):
    print(f"[*] hold {slot_name} for {binary_path}")

    script = "./decompilation_ida/cg_extractor_for_stripped.py"
    run_ida(ida_path, script, binary_path)

    ida_slots.append(slot_name)
    print(f"[+] release {slot_name} after finishing {binary_path}")


def main(args):
    binary_path = args.binary_path

    def submit_task(binary_file_path):
        while len(ida_slots) == 0:
            print("Wait for ida slot: 1 sec")
            time.sleep(1)

        slot = ida_slots.pop()

        executor.submit(
            process_unstripped_binary if args.unstripped else process_stripped_binary,
            ida_path=args.ida_path,
            slot_name=slot,
            binary_path=binary_file_path,
        )

    if os.path.isfile(binary_path):
        if is_binary_file(binary_path):
            print(f"[+] start to process {binary_path}")
            submit_task(binary_path)

    elif os.path.isdir(binary_path):
        for root, _, files in os.walk(binary_path):
            for file in files:
                binary_file_path = os.path.join(root, file)
                if is_binary_file(binary_file_path):
                    print(f"[+] start to process {binary_file_path}")

                    submit_task(binary_file_path)

                    while executor._work_queue.qsize() > thread_num:
                        print("Wait for executor:", executor._work_queue.qsize())
                        time.sleep(1)
    else:
        print(f"Check your {binary_path}.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Perform parallel call graph extraction for binaries')
    parser.add_argument('-u', '--unstripped', action='store_true',
                        help="Indicates that the binary is unstripped (contains debug symbols).")
    parser.add_argument('-s', '--stripped', action='store_true',
                        help="Indicates that the binary is stripped (lacks debug symbols).")
    parser.add_argument('-b', '--binary_path', type=str, required=True,
                        help="Specify the path to the binary file or folder containing binaries.")
    parser.add_argument('-i', '--ida_path', type=str, required=True,
                        help="Path to idat or idat64")
    args = parser.parse_args()

    if args.unstripped == True and args.stripped == True or args.unstripped == False and args.stripped == False:
        print("Error! You can just choose one mode '-u' or '-s'")
        sys.exit(0)

    args = parser.parse_args()
    main(args)