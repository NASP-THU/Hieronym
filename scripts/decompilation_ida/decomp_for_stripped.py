import os
import json
import idaapi
import idautils
import idc
import re
import ida_pro

MASK = True
OUTPUT_BASE = ""

def mask_function_definition(code):
    lines = code.splitlines()
    if not lines:
        return code
    for i, line in enumerate(lines):
        match = re.search(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b(?=\s*\()', line)
        if match:
            func_name = match.group(1)
            lines[i] = re.sub(re.escape(func_name), '[MASK]', line, count=1)
            break
    return '\n'.join(lines)

def get_function_asm(func_ea):
    asm_lines = []

    for insn_ea in idautils.FuncItems(func_ea):
        disasm = idc.GetDisasm(insn_ea)
        asm_lines.append(disasm)

    return asm_lines

def get_decompiled_function(func_ea):
    try:
        cfunc = idaapi.decompile(func_ea)
        if not cfunc:
            return None
        return str(cfunc)
    except Exception:
        return None

def extract_functions():
    functions = {}
    success, failed = 0, 0
    for ea in idautils.Functions():
        func = idaapi.get_func(ea)
        if not func:
            continue
        segname = get_segm_name(func.start_ea)

        if segname[1:3] not in ["OA", "OM", "te"]:
            continue

        name = idc.get_func_name(ea)
        start = func.start_ea
        end = func.end_ea
        print(f"[*] Processing function: {name} {hex(start)}")
        assembly = get_function_asm(start)
        decompiled = get_decompiled_function(start)
        if decompiled:
            original_decompiled = decompiled
            if MASK:
                decompiled = mask_function_definition(decompiled)

            functions[str(hex(start))] = {
                "assembly": assembly,
                "decomp_code": original_decompiled,
                "mask_code": decompiled,
                'function_address': {
                    'start': str(hex(start)),
                    'end': str(hex(end)),
                },
                "func_name": name
            }

            success += 1
        else:
            print(f"[!] Failed to decompile function: {name}")
            failed += 1
    print(f"[+] Total functions: {(success + failed)} Decompiled: {success}  Failed: {failed}")
    return functions

def main():
    idaapi.auto_wait()

    file_path = idaapi.get_input_file_path()
    print(f"1. load the binary file: {file_path}")

    assert (
        OUTPUT_BASE
    ), "Please provide the dir to save the results in 'decompilation_ida/decomp_for_stripped.py'"

    output_dir = '/'.join(file_path.split('/')[-4:-1])
    output_dir = os.path.join(OUTPUT_BASE, output_dir)
    binary_name = os.path.basename(file_path)

    output_file_path = os.path.join(output_dir, binary_name+ '.json')

    if not os.path.exists(output_dir):
        print(f"2. create the output folder: {output_dir}")
        os.makedirs(output_dir)
    print("3. decompile function: ")

    functions = extract_functions()

    with open(output_file_path, 'w') as f:
        print(f"4. write result to output_file_path:  {output_file_path}")
        json.dump(functions, f, indent=4)

    ida_pro.qexit(0)

if __name__ == "__main__":
    main()

