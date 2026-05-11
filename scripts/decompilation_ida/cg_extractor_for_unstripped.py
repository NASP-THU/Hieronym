import idaapi
import idautils
import idc
import ida_hexrays
import json
import os
import ida_pro

OUTPUT_BASE = ""


def clean_import_name(name):
    if '@@' in name:
        return name.split('@@')[0]
    if '@' in name:
        return name.split('@')[0]
    return name


def get_external_and_export_functions():
    external_funcs = set()
    export_funcs = set()

    # exports
    for _, _, ea, name in idautils.Entries():
        if not name:
            name = idc.get_func_name(ea)
        if name:
            export_funcs.add(name)

    # imports
    def imp_cb(ea, name, ord):
        if name:
            clean_name = clean_import_name(name)
            external_funcs.add(clean_name)
        return True

    for i in range(idaapi.get_import_module_qty()):
        idaapi.enum_import_names(i, imp_cb)

    return external_funcs, export_funcs


def init_hexrays():
    if not ida_hexrays.init_hexrays_plugin():
        print("[!] Hex-Rays not available")
        return False
    return True


def get_function_name(func):
    return idc.get_func_name(func.start_ea)


def extract_calls(func, caller_name, call_relations, external_funcs):
    try:
        start_ea = func.start_ea
        end_ea = func.end_ea

        for head in idautils.Heads(start_ea, end_ea):

            if idaapi.is_call_insn(head):
                target = idc.get_operand_value(head, 0)

                process_call(
                    target,
                    caller_name,
                    call_relations,
                    external_funcs
                )

    except Exception as e:
        print(f"[!] Error extracting {caller_name} {e}")


def process_call(call_ea, caller_name, call_relations, external_funcs):
    callee_func = idaapi.get_func(call_ea)

    if callee_func:
        callee_name = idc.get_func_name(callee_func.start_ea)
        tmp_name = callee_name.strip(".")
        if tmp_name in external_funcs:
            if tmp_name not in call_relations[caller_name]['callee_external']:
                call_relations[caller_name]['callee_external'].append(callee_name.strip("."))
        else:
            if callee_name not in call_relations[caller_name]['callee_internal']:
                call_relations[caller_name]['callee_internal'].append(callee_name)

            if callee_name not in call_relations:
                call_relations[callee_name] = create_empty_entry(callee_func)
            if caller_name not in call_relations[callee_name]['caller']:
                call_relations[callee_name]['caller'].append(caller_name)
    else:
        # external or unresolved
        name = idc.get_name(call_ea)
        if not name:
            name = "EXT_" + str(call_ea)

        if name not in call_relations[caller_name]['callee_external']:
            call_relations[caller_name]['callee_external'].append(name)


def create_empty_entry(func):
    return {
        "caller": [],
        "callee_internal": [],
        "callee_external": [],
        "address": hex(func.start_ea),
        "is_external": False
    }


def build_complete_relationships(call_relations):
    all_funcs = list(call_relations.keys())

    for func in all_funcs:
        for callee in call_relations[func]['callee_internal']:
            if callee not in call_relations:
                call_relations[callee] = {
                    "caller": [],
                    "callee_internal": [],
                    "callee_external": [],
                    "address": "unknown",
                    "is_external": False
                }
            if func not in call_relations[callee]['caller']:
                call_relations[callee]['caller'].append(func)


def main():
    idaapi.auto_wait()
    print("[*] Start extracting call graph (IDA)")

    if not init_hexrays():
        return

    external_funcs, export_funcs = get_external_and_export_functions()

    call_relations = {}

    for ea in idautils.Functions():
        func = idaapi.get_func(ea)
        if not func:
            continue

        segname = get_segm_name(func.start_ea)

        if segname[1:4] in ["plt"]:
            external_funcs.add(idc.get_func_name(ea))

    for func_ea in idautils.Functions():
        func = idaapi.get_func(func_ea)
        if not func:
            continue

        func_name = get_function_name(func)

        if func_name in external_funcs:
            continue

        segname = get_segm_name(func.start_ea)

        if segname[1:3] not in ["OA", "OM", "te"]:
            continue

        if func_name not in call_relations:
            call_relations[func_name] = {
                "caller": [],
                "callee_internal": [],
                "callee_external": [],
                "address": hex(func.start_ea),
                "is_external": False
            }

        extract_calls(func, func_name, call_relations, external_funcs)

    build_complete_relationships(call_relations)

    file_path = idaapi.get_input_file_path()
    binary_name = os.path.basename(file_path)

    output_dir = '/'.join(file_path.split('/')[-4:-1])
    output_dir = os.path.join(OUTPUT_BASE, output_dir)

    output_file_path = os.path.join(output_dir, binary_name + '.json')

    if not os.path.exists(output_dir):
        print(f"2. create the output folder: {output_dir}")
        os.makedirs(output_dir)
    print("3. decompile function: ")

    with open(output_file_path, 'w') as f:
        print(f"4. write result to output_file_path: {output_file_path}")
        json.dump(call_relations, f, indent=4)

    print(f"[*] CG saved to {output_file_path}")

    ida_pro.qexit(0)


if __name__ == "__main__":
    main()
