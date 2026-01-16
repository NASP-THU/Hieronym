import os
import json
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

file_path = str(getProgramFile())
print("1. load the binary file: ", file_path)
output_dir = ""
assert (
    output_dir
), "Please provide the dir to save the results in 'decompilation/decompile_for_stripped.py'"
output_file_path = os.path.join(output_dir, file_path.split('/')[-1] + '.json')


if not os.path.exists(output_dir):
    print("2. create the output folder: ", output_dir)
    os.makedirs(output_dir)

def get_data_type_info(f, var, is_arg, count):
    # variable name and type
    varname = var.getName()
    type_object = var.getDataType()
    type_name = type_object.getName()

    # get to what ever the pointer is pointing to
    ptr_bool = False
    for _ in range(type_name.count('*')):
        type_object = type_object.getDataType()
        type_name = type_object.getName()
        ptr_bool = True

    # if a typedef, get the primitive type definition
    try:
        type_object = type_object.getBaseDataType()
        type_name = type_object.getName()
    except:
        pass

    # find if struct, union, enum, or none of the above
    is_struct = False
    is_union = False
    if len(str(type_object).split('\n')) >= 2:
        if 'Struct' in str(type_object).split('\n')[2]:
            is_struct = True
        elif 'Union' in str(type_object).split('\n')[2]:
            is_union = True

    try:
        type_object.getCount()
        is_enum = True
    except:
        is_enum = False

    if ptr_bool:
        type_name += ' *'

    f[varname] = {'type': str(type_name), 'addresses': [],
                  'agg': {'is_enum': is_enum, 'is_struct': is_struct, 'is_union': is_union}}

    locs = ref.getReferencesTo(var)
    for loc in locs:
        f[varname]['addresses'].append(loc.getFromAddress().toString())

    if is_arg:
        # need to store the register the args are saved into.
        f[varname]['register'] = var.getRegister().getName()
        f[varname]['count'] = count

    return f


getCurrentProgram().setImageBase(toAddr(0), 0)
ref = currentProgram.getReferenceManager()
currentProgram = getCurrentProgram()
listing = currentProgram.getListing()
function = getFirstFunction()

ifc = DecompInterface()
ifc.openProgram(currentProgram)

res = {}


print("3. decompile function: ")
while function is not None:
    print('\t', function.name)
    funcname = function.name
    addrSet = function.getBody()
    codeUnits = listing.getCodeUnits(addrSet, True)

    all_vars = function.getAllVariables()
    all_args = function.getParameters()

    assembly = []

    for codeUnit in codeUnits:
        instruction = codeUnit.toString()
        assembly.append(instruction)
    
    # regular stack vars
    var_metadata = {}
    for var in all_vars:
        var_metadata = get_data_type_info(var_metadata, var, False, -1)

    # function args
    args_metadata = {}
    for arg in all_args:
        count = 0
        if arg.getRegister() is not None:
            args_metadata = get_data_type_info(args_metadata, arg, True, count)
            count += 1

    decomp = ifc.decompileFunction(function, 60, ConsoleTaskMonitor())
    decompiled_function = decomp.getDecompiledFunction().getC()

    res[str(function.getEntryPoint())] = {
        "assembly": assembly,
        "decomp_code": decompiled_function,
        "variable_metadata": var_metadata,
        "args_metadata": args_metadata,
        'function_address': {
            'start': str(function.getEntryPoint()),
            'end': str(function.getBody().getMaxAddress()),
        },
        "func_name": funcname
    }

    function = getFunctionAfter(function)

with open(output_file_path, 'w') as f:
    print("4. write result to output_file_path: ", output_file_path)
    json.dump(res, f, indent=4)
