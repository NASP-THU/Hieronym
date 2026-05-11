import os
import json
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

file_path = str(getProgramFile())
print("1. load the binary file: ", file_path)
output_dir = ""
assert (
    output_dir
), "Please provide the dir to save the results in 'process_single_binary/decomp_for_stripped.py'"
output_file_path = os.path.join(output_dir, file_path.split('/')[-1] + '.json')


if not os.path.exists(output_dir):
    print("2. create the output folder: ", output_dir)
    os.makedirs(output_dir)

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
    funcname = function.name
    addrSet = function.getBody()
    codeUnits = listing.getCodeUnits(addrSet, True)

    assembly = []

    for codeUnit in codeUnits:
        instruction = codeUnit.toString()
        assembly.append(instruction)


    try:
        decomp = ifc.decompileFunction(function, 60, ConsoleTaskMonitor())
        decompiled_function = decomp.getDecompiledFunction().getC()
    except:
        decompiled_function = ''

    res[str(function.getEntryPoint())] = {
        "assembly": assembly,
        "decomp_code": decompiled_function,
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
