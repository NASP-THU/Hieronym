import json
import os
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from ghidra.program.model.pcode import PcodeOp
from ghidra.program.model.symbol import SourceType, SymbolType
from ghidra.app.script import GhidraScript

class SimplifiedCGFromDecompiler(GhidraScript):
    def run(self):

        output_dir = ''

        file_path = str(getProgramFile())
        binary_name = os.path.basename(file_path)
        output_file = os.path.join(output_dir, binary_name+'.json')
        
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        getCurrentProgram().setImageBase(toAddr(0), 0)
        current_program = getCurrentProgram()

        decompiler = DecompInterface()
        decompiler.openProgram(current_program)
        
        func_manager = current_program.getFunctionManager()
        all_functions = list(func_manager.getFunctions(True))

        function = getFirstFunction()
        external_functions, exported_functions = self.get_external_functions()

        function_identifier_map = {}
        for func in all_functions:
            func_name = func.getName()
            func_id = self.generate_function_identifier(func, external_functions)
            function_identifier_map[func_name] = func_id
        
        call_relations = {}
        monitor = ConsoleTaskMonitor()
        
        while function is not None:
            func_name = function.getName()

            if func_name in external_functions:
                function = getFunctionAfter(function)
                continue

            display_name = function_identifier_map[func_name]

            if display_name not in call_relations:
                call_relations[display_name] = {
                    'caller': [],
                    'callee_internal': [],
                    'callee_external': [],
                    'address': str(function.getEntryPoint()),
                    'is_external': False,
                    'func_name': func_name
                }

            if func_name in exported_functions:
                call_relations[display_name]['address'] = str(function.getEntryPoint())
            
            try:
                decompile_results = decompiler.decompileFunction(function, 60, monitor)
                if decompile_results.decompileCompleted():
                    high_func = decompile_results.getHighFunction()
                    if high_func:
                        self.extract_calls_from_high_func(
                            high_func, 
                            display_name, 
                            call_relations,
                            function_identifier_map, 
                            external_functions
                        )
                else:
                    print('{} error to decompiled!'.format(func_name))
            except Exception as e:
                print("[!] Error analyzing function {}: {}".format(display_name, e))

            function = getFunctionAfter(function)

        self.build_complete_relationships(call_relations)

        with open(output_file, 'w') as f:
            json.dump(call_relations, f, indent=2)

        print('[*] CG saved to: {}'.format(output_dir))


    def generate_function_identifier(self, function, external_functions):
        func_name = function.getName()
        if func_name in external_functions:
            return func_name
        return str(function.getEntryPoint())

    def get_external_functions(self):
        external_funcs = set()
        export_funcs = set()

        symbol_table = currentProgram.getSymbolTable()
        for symbol in symbol_table.getDefinedSymbols():
            if symbol.getSymbolType() == SymbolType.FUNCTION:
                export_funcs.add(symbol.getName())

        for symbol in symbol_table.getExternalSymbols():
            if symbol.getSymbolType() == SymbolType.FUNCTION:
                external_funcs.add(symbol.getName())

        external_manager = currentProgram.getExternalManager()
        for lib in external_manager.getExternalLibraryNames():
            for loc in external_manager.getExternalLocations(lib):
                external_funcs.add(loc.getLabel())

        return external_funcs, export_funcs

    def extract_calls_from_high_func(self, high_func, caller_name, call_relations, function_identifier_map, external_functions):
        try:
            pcode_op_iter = high_func.getPcodeOps()
            while pcode_op_iter.hasNext():
                pcode_op = pcode_op_iter.next()
                if pcode_op.getOpcode() == PcodeOp.CALL:
                    self.process_direct_call(
                        pcode_op, 
                        caller_name, 
                        call_relations,
                        function_identifier_map, 
                        external_functions
                    )
        except Exception as e:
            print("[!] Error extracting calls for {}: {}".format(caller_name, e))

    def process_direct_call(self, pcode_op, caller_name, call_relations, function_identifier_map, external_functions):
        input0 = pcode_op.getInput(0)
        if not input0:
            return
        
        call_address = input0.getAddress()
        if not call_address:
            return
        
        callee_func = getFunctionAt(call_address)
        if callee_func:
            callee_name = callee_func.getName()
            display_name = function_identifier_map[callee_name]

            if callee_name in external_functions:
                if callee_name not in call_relations[caller_name]['callee_external']:
                    call_relations[caller_name]['callee_external'].append(callee_name)
            else:
                if display_name not in call_relations[caller_name]['callee_internal']:
                    call_relations[caller_name]['callee_internal'].append(display_name)
                    if display_name not in call_relations:
                        call_relations[display_name] = {
                            'caller': [caller_name],
                            'callee_internal': [],
                            'callee_external': [],
                            'address': str(callee_func.getEntryPoint()),
                            'is_external': False,
                            'func_name': callee_name
                        }
                    else:
                        if caller_name not in call_relations[display_name]['caller']:
                            call_relations[display_name]['caller'].append(caller_name)
        else:
            external_label = self.get_external_function_label(call_address)
            if external_label and external_label in external_functions:
                if external_label not in call_relations[caller_name]['callee_external']:
                    call_relations[caller_name]['callee_external'].append(external_label)

    def get_external_function_label(self, address):
        try:
            symbol_table = currentProgram.getSymbolTable()
            symbols = symbol_table.getSymbols(address)
            for symbol in symbols:
                if symbol.getSource() == SourceType.IMPORTED:
                    return symbol.getName()

            ref_manager = currentProgram.getReferenceManager()
            refs = ref_manager.getReferencesFrom(address)
            for ref in refs:
                if ref.getReferenceType().isCall():
                    to_addr = ref.getToAddress()
                    symbol = symbol_table.getPrimarySymbol(to_addr)
                    if symbol:
                        return symbol.getName()

            return "EXT_{:08x}".format(address.getOffset())
        except Exception as e:
            print("[!] Error getting external label: {}".format(e))
            return "EXT_{:08x}".format(address.getOffset())

    def create_empty_function_entry(self, func_name):
        addr = "unknown"
        try:
            func = self.getFunction(func_name)
            if func:
                addr = str(func.getEntryPoint())
        except:
            pass

        return {
            'caller': [],
            'callee_internal': [],
            'callee_external': [],
            'address': addr,
            'is_external': False,
            'func_name': None
        }

    def build_complete_relationships(self, call_relations):
        all_funcs = list(call_relations.keys())
        for func in all_funcs:
            for callee in call_relations[func]['callee_internal']:
                if callee not in call_relations:
                    call_relations[callee] = self.create_empty_function_entry(callee)
                if func not in call_relations[callee]['caller']:
                    call_relations[callee]['caller'].append(func)
            
            for caller in call_relations[func]['caller']:
                if caller not in call_relations:
                    call_relations[caller] = self.create_empty_function_entry(caller)
                if func not in call_relations[caller].get('callee_internal', []) and \
                   func not in call_relations[caller].get('callee_external', []):
                    call_relations[caller]['callee_internal'].append(func)


if __name__ == '__main__':
    SimplifiedCGFromDecompiler().run()
