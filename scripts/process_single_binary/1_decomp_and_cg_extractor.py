import os.path
from concurrent.futures import ThreadPoolExecutor
import time
import subprocess
import argparse


thread_num = 1
executor = ThreadPoolExecutor(max_workers=thread_num)
ghidra_projects = [f'parser_{i}/' for i in range(1)]


def process_stripped_binary(ghidra_path, project_path, project_name, binary_path):
    print(f"[*] hold {project_name} for {binary_path}")
    cmd = f"{ghidra_path} {project_path} {project_name} -import {binary_path} -readOnly -postScript ./process_single_binary/decomp_for_stripped.py"
    try:
        subprocess.run(cmd, shell=True, timeout=900*4)
    except subprocess.TimeoutExpired:
        print(f"[!] timeout for {binary_path}")
    ghidra_projects.append(project_name)
    print(f"[+] release {project_name} after finishing {binary_path}")



def process_stripped_binary_for_call(ghidra_path, project_path, project_name, binary_path):
    print(f"[*] hold {project_name} for {binary_path}")
    cmd = f"{ghidra_path} {project_path} {project_name} -import {binary_path} -readOnly -postScript ./process_single_binary/cg_extractor_for_stripped.py"
    try:
        subprocess.run(cmd, shell=True, timeout=900*4)
    except subprocess.TimeoutExpired:
        print(f"[!] timeout for {binary_path}")
    ghidra_projects.append(project_name)
    print(f"[+] release {project_name} after finishing {binary_path}")



def main(args):
    binary_path = args.binary_path
    if os.path.isfile(binary_path):
        print(f"[+] start to process {binary_path}")
        
        ghidra_project = ghidra_projects.pop()
        executor.submit(process_stripped_binary,
                        ghidra_path=args.ghidra_path,
                        project_path=args.project_path,
                        project_name=ghidra_project,
                        binary_path=binary_path,)
        while len(ghidra_projects) == 0:
            print("Wait for ghidra project: 60 sec")
            time.sleep(60)

        ghidra_project = ghidra_projects.pop()
        executor.submit(process_stripped_binary_for_call,
                        ghidra_path=args.ghidra_path,
                        project_path=args.project_path,
                        project_name=ghidra_project,
                        binary_path=binary_path,)
    else:
        print(f"Check your {binary_path}.")
    



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Perform decompilation and extract the call graph from binaries.')
    parser.add_argument('-b', '--binary_path', type=str, required=True,
        help="Specify the path to the binary file.")
    parser.add_argument('-g', '--ghidra_path', type=str, required=True,
        help="Provide the path to the Ghidra 'analyzeHeadless'.")
    parser.add_argument('-p', '--project_path', type=str, required=True,
        help="Specify the directory path to Ghidra projects.")
    args = parser.parse_args()

    if not os.path.exists(args.project_path):
        os.makedirs(args.project_path)

    main(args)
