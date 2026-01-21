import os
import sys
import argparse
import tree_sitter_cpp as tsc
from tree_sitter import Language, Parser
from tqdm import tqdm
import re
from collections import deque, defaultdict
import json
import tempfile
import shutil
from typing import Dict, Any, Iterator, List, Tuple
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
import atexit
import itertools

TEMP_DIR = None
BLOCK_START_SEPARATOR = '\n' + '//' + '=' * 15 + 'block id start' + '=' * 15 + '\n'
BLOCK_END_SEPARATOR = '\n' + '//' + '=' * 15 + 'block id end' + '=' * 15 + '\n'
MAX_LINE_INTERVAL = 9


class ASTProcessor:
    def __init__(self):
        self.node_counter = 0
        self.parser = self._init_parser()
        self.node_map = {}
        self.max_line_filter = 4
        self.collapse_node_types = ['else_clause', 'case_statement']
        self.logic_node_types = {
            'if_statement',
            'while_statement',
            'for_statement',
            'switch_statement',
            'function_definition',
            'do_statement',
            'labeled_statement',
            'goto_statement'
        }

    def _init_parser(self):
        c_language = Language(tsc.language())
        parser = Parser(c_language)
        return parser

    def parse_to_list(self, code):
        code_bytes = bytes(code, 'utf-8')
        tree = self.parser.parse(code_bytes)
        root_node = tree.root_node
        ast = self.extract_logic_nodes(root_node)

        self.node_counter = 0
        self.node_map = {}

        subtrees = []
        if ast is not None:
            root_id = self._build_flat_ast(ast)
            if len(self.node_map[root_id]['children']) > 0:
                for node_id in self.node_map[root_id]['children']:
                    subtree = []
                    self._build_subtree(node_id, subtree)
                    subtrees.append(subtree)
            else:
                subtrees.append([self.node_map[root_id]])

        return subtrees

    def extract_logic_nodes(self, root_node):
        def traverse(node):
            num_lines = node.end_point.row - node.start_point.row
            result = {
                "id": node.id,
                'type': node.type,
                'value': node.text.decode('utf8'),
                'line': (node.start_point.row, node.end_point.row + 1),
                'children': []
            }

            if node.type in self.logic_node_types:
                if num_lines > self.max_line_filter:
                    for child in node.children:
                        if child.type not in self.collapse_node_types:
                            child_result = traverse(child)
                            if child_result:
                                result['children'].append(child_result)
                    return result
                else:
                    return None
            else:
                temp_children = []
                if num_lines > self.max_line_filter:
                    for child in node.children:
                        if child.type not in self.collapse_node_types:
                            child_result = traverse(child)
                            if child_result:
                                temp_children.append(child_result)

                    if temp_children:
                        return {
                            "id": node.id,
                            'type': node.type,
                            'value': node.text.decode('utf8'),
                            'line': (node.start_point.row, node.end_point.row + 1),
                            'children': temp_children,
                            'is_logic': False
                        }
                return None

        logic_ast = traverse(root_node)
        return logic_ast

    def _build_flat_ast(self, node):
        node_id = self.node_counter
        self.node_counter += 1

        self.node_map[node_id] = {
            "id": node_id,
            "type": node['type'],
            "value": node['value'],
            'line': node['line'],
            "children": [self._build_flat_ast(child) for child in node['children']]
        }
        return node_id

    def _build_subtree(self, target_id, subtree):
        node = self.node_map[target_id]
        subtree.append({
            "id": node["id"],
            "type": node["type"],
            "value": node["value"],
            'line': node['line'],
            "children": node["children"].copy()
        })

        for node_id in self.node_map[target_id]['children']:
            self._build_subtree(node_id, subtree)


def remove_all_comments(code):
    pattern = r'''
        ("(?:\\.|[^"\\])*")        # Match string
        |(/\*.*?\*/)               # Match /* */ comment
        |(//.*?$)                  # Match // Comment
    '''
    return re.sub(pattern, lambda m: m.group(1) if m.group(1) else '',
                  code, flags=re.DOTALL | re.MULTILINE | re.VERBOSE)


def remove_comments(code):
    code = re.sub(r'//.*$', '', code, flags=re.MULTILINE)
    code = '\n'.join(line for line in code.split('\n') if line.strip())
    return code


def get_subtree_levels(nodes, root_id):
    node_dict = {node['id']: node for node in nodes}
    if root_id not in node_dict:
        return []

    result = []
    queue = deque([root_id])

    while queue:
        level_size = len(queue)
        current_level = []

        for _ in range(level_size):
            node_id = queue.popleft()
            node = node_dict[node_id]
            current_level.append(node)

            for child_id in node['children']:
                queue.append(child_id)

        result.append(current_level)

    return result


def split_by_children_and_level(nodes, parent_id):
    node_dict = {node['id']: node for node in nodes}
    if parent_id not in node_dict:
        return {}

    subtree_roots = node_dict[parent_id]['children']
    result = {}
    for root_id in subtree_roots:
        subtree_levels = get_subtree_levels(nodes, root_id)
        result[root_id] = subtree_levels

    return result


def create_node_from_ast(ast_node, node_id='', children=[]):
    if not node_id:
        return {
            "id": ast_node['id'],
            "type": ast_node["type"],
            "value": ast_node["value"],
            'line': ast_node['line'],
            "children": children
        }
    else:
        return {
            "id": node_id,
            "type": ast_node["type"],
            "value": ast_node["value"],
            'line': ast_node['line'],
            "children": children
        }


def get_ast_blocks(source_code, func_name):
    processor = ASTProcessor()
    ast_list = processor.parse_to_list(source_code)
    if not ast_list:
        return None, None

    ast_list = ast_list[0]
    filtered_ast = defaultdict(dict)
    output = defaultdict(list)

    if len(ast_list) > 2:
        root_node = create_node_from_ast(ast_list[0], '1', ast_list[1]['children'])
        output[ast_list[0]['id']].append(root_node)
        result = split_by_children_and_level(ast_list, 2)
    else:
        root_node = create_node_from_ast(ast_list[0], '1', ast_list[0]['children'])
        output[ast_list[0]['id']].append(root_node)
        result = split_by_children_and_level(ast_list, 1)

    for subtree_root, levels in result.items():
        skipped_nodes = set()
        for level in levels:
            if len(level) > 1:
                level.sort(key=lambda x: x['id'], reverse=True)
            for node in level:
                if node['id'] not in skipped_nodes:
                    if len(node['children']) == 1:
                        child_id = node['children'][0] - 1
                        if ast_list[child_id]['type'] in ['compound_statement']:
                            skipped_nodes.add(ast_list[child_id]['id'])
                            merged_node = create_node_from_ast(node, children=ast_list[child_id]['children'])
                            filtered_ast[node['id']] = merged_node
                            output[ast_list[subtree_root - 1]['id']].insert(0, merged_node)
                        else:
                            filtered_ast[node['id']] = node
                            output[ast_list[subtree_root - 1]['id']].insert(0, node)
                    else:
                        filtered_ast[node['id']] = node
                        output[ast_list[subtree_root - 1]['id']].insert(0, node)

    return filtered_ast, output


def remove_and_renumber_nodes(ast_blocks, source_code):
    func_blocks = source_code
    src_lines = source_code.split('\n')
    block_id_map = {}
    block_counter = 1

    total_blocks = -1
    for blocks in ast_blocks.values():
        total_blocks += len(blocks)

    for key, blocks in ast_blocks.items():
        if key in (0, 1):
            continue
        for bid in range(len(blocks) - 1, -1, -1):
            block_id_map[blocks[bid]['id']] = str(total_blocks - block_counter + 1)
            start_line, end_line = blocks[bid]['line']
            code_block = '\n'.join(src_lines[start_line:end_line])
            block_id = str(total_blocks - block_counter + 1)
            replacement = (
                BLOCK_START_SEPARATOR.replace('id', block_id) +
                code_block +
                BLOCK_END_SEPARATOR.replace('id', block_id)
            )
            func_blocks = func_blocks.replace(code_block, replacement)
            block_counter += 1

    return block_id_map, func_blocks


def remove_overlapping_blocks(ast_list, blocks_list):
    skipped_nodes = set()
    output = defaultdict(list)

    for key, blocks in blocks_list.items():
        for bid in range(len(blocks) - 1, -1, -1):
            node = blocks[bid]
            if node['id'] not in skipped_nodes:
                if len(node['children']) == 1:
                    child_id = node['children'][0]
                    child_start, child_end = ast_list[child_id]['line']
                    cur_start, cur_end = node['line']
                    line_gap = (child_start - cur_start) + (cur_end - child_end)

                    if line_gap < MAX_LINE_INTERVAL:
                        skipped_nodes.add(ast_list[child_id]['id'])
                        merged_node = create_node_from_ast(node, children=ast_list[child_id]['children'])
                    else:
                        merged_node = node
                    output[key].insert(0, merged_node)
                else:
                    output[key].insert(0, node)
    return output


def format_output_line(source_code, func_blocks, block_data, block_id_map, func_type, func_name, training):
    output = defaultdict(dict)

    if training:
        for key, blocks in block_data.items():
            if key in (0, 1):
                func_node = blocks[0]
                func_relations = [block_id_map[fid] for fid in func_node['children']]
            else:
                for block in blocks:
                    block_id = block_id_map[block['id']]
                    child_relations = [block_id_map[cid] for cid in block['children']]
                    output[func_type][block_id] = {
                        'type': block['type'],
                        'code': block['line'],
                        'relation': child_relations
                    }
        output[func_type][func_name] = {
            'type': func_node['type'],
            'code': source_code,
            'relation': func_relations,
            'block_code': func_blocks
        }
    else:
        for key, blocks in block_data.items():
            if key in (0, 1):
                func_node = blocks[0]
                func_relations = [block_id_map[fid] for fid in func_node['children']]
            else:
                for block in blocks:
                    block_id = block_id_map[block['id']]
                    child_relations = [block_id_map[cid] for cid in block['children']]
                    output[block_id] = {
                        'type': block['type'],
                        'code': block['line'],
                        'relation': child_relations
                    }
        output[func_name] = {
            'type': func_node['type'],
            'code': source_code,
            'relation': func_relations,
            'block_code': func_blocks
        }
    return output


def format_output_code(source_code, func_blocks, block_data, block_id_map, func_type, func_name, training):
    output = defaultdict(dict)

    if training:
        for key, blocks in block_data.items():
            if key in (0, 1):
                func_node = blocks[0]
                func_relations = [block_id_map[fid] for fid in func_node['children']]
            else:
                for block in blocks:
                    block_id = block_id_map[block['id']]
                    child_relations = [block_id_map[cid] for cid in block['children']]
                    output[func_type][block_id] = {
                        'type': block['type'],
                        'code': block['value'],
                        'relation': child_relations
                    }
        output[func_type][func_name] = {
            'type': func_node['type'],
            'code': source_code.strip(),
            'relation': func_relations,
            'block_code': func_blocks.strip()
        }
    else:
        for key, blocks in block_data.items():
            if key in (0, 1):
                func_node = blocks[0]
                func_relations = [block_id_map[fid] for fid in func_node['children']]
            else:
                for block in blocks:
                    block_id = block_id_map[block['id']]
                    child_relations = [block_id_map[cid] for cid in block['children']]
                    output[block_id] = {
                        'type': block['type'],
                        'code': block['value'],
                        'relation': child_relations
                    }
        output[func_name] = {
            'type': func_node['type'],
            'code': source_code.strip(),
            'relation': func_relations,
            'block_code': func_blocks.strip()
        }
    return output


def cleanup_temp_dir():
    if TEMP_DIR and os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)


def init_worker_temp_dir(temp_dir):
    global TEMP_DIR
    TEMP_DIR = temp_dir


def parse_code_file(file_path: str) -> Tuple[str, str, str, str]:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if len(lines) < 4:
            return None

        file_path_line = lines[0].strip()
        func_name = lines[2].strip()
        source_code = ''.join(lines[3:])
        return func_name, source_code, 'src_code', file_path_line
    except Exception as e:
        return None


def process_file_batch(args) -> str:
    file_paths: List[str] = args[0]
    config_dict: Dict = args[1]
    part_id: int = args[2]

    temp_file = os.path.join(config_dict['temp_dir'], f"part_{part_id:06d}.json")
    batch_results = {}

    for file_path in file_paths:
        try:
            parsed = parse_code_file(file_path)
            if not parsed:
                continue
            func_name, source_code, func_type, _ = parsed

            is_split = len(source_code.split('\n')) >= 17
            if is_split:
                ast_list, ast_json = get_ast_blocks(source_code, func_name)
                if ast_json:
                    ast_json = remove_overlapping_blocks(ast_list, ast_json)
                else:
                    ast_json = {0: [{'type': "full_function", 'children': []}]}
            else:
                ast_json = {0: [{'type': "full_function", 'children': []}]}

            block_id_map, func_blocks = remove_and_renumber_nodes(ast_json, source_code)
            function_map = format_output_line(
                source_code, func_blocks, ast_json, block_id_map, func_type, func_name, config_dict['training']
            )

            if config_dict['training']:
                batch_results.update(function_map)
            else:
                batch_results[func_name] = function_map

        except Exception:
            continue

    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(batch_results, f, ensure_ascii=False, indent=None)

    return temp_file


def process_function_batch(args) -> str:
    func_items: List[tuple] = args[0]
    config_dict = args[1]
    part_id = args[2]

    temp_file = os.path.join(config_dict['temp_dir'], f"part_{part_id:06d}.json")
    batch_results = {} if config_dict['training'] else []

    for func_name, source_code in func_items:
        try:
            is_split = len(source_code.split('\n')) >= 17
            if is_split:
                ast_list, ast_json = get_ast_blocks(source_code, func_name)
                if ast_json:
                    ast_json = remove_overlapping_blocks(ast_list, ast_json)
                else:
                    ast_json = {0: [{'type': "full_function", 'children': []}]}
            else:
                ast_json = {0: [{'type': "full_function", 'children': []}]}

            block_id_map, func_blocks = remove_and_renumber_nodes(ast_json, source_code)
            function_map = format_output_line(
                source_code, func_blocks, ast_json, block_id_map, 'src_code', func_name, config_dict['training']
            )

            if config_dict['training']:
                batch_results.update(function_map)
            else:
                batch_results.append(function_map)

        except Exception:
            continue

    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(batch_results, f, ensure_ascii=False, indent=None)

    return temp_file


def process_single_function_batch(source_code, func_type, func_name, config_dict, is_split, is_test=False) -> Dict:
    if is_split:
        ast_list, ast_json = get_ast_blocks(source_code, func_name)
        if ast_json:
            ast_json = remove_overlapping_blocks(ast_list, ast_json)
        else:
            ast_json = {0: [{'type': "full_function", 'children': []}]}
    else:
        ast_json = {0: [{'type': "full_function", 'children': []}]}

    block_id_map, func_blocks = remove_and_renumber_nodes(ast_json, source_code)
    if is_test:
        function_map = format_output_code(
            source_code, func_blocks, ast_json, block_id_map, func_type, func_name, config_dict['training']
        )
    else:
        function_map = format_output_line(
            source_code, func_blocks, ast_json, block_id_map, func_type, func_name, config_dict['training']
        )
    return function_map


def process_test_function_batch(args) -> str:
    func_items: List[Dict] = args[0]
    config_dict = args[1]
    part_id = args[2]

    temp_file = os.path.join(config_dict['temp_dir'], f"part_{part_id:06d}.json")
    batch_results = defaultdict(dict) if config_dict['training'] else []

    for func_item in func_items:
        for func_name, merge_code in func_item.items():
            try:
                function_map = defaultdict(dict)
                decom_code = remove_all_comments(merge_code['stripped'])
                is_split = len(decom_code.split('\n')) >= 17
                decom_function_map = process_single_function_batch(
                    decom_code, 'stripped', func_name, config_dict, is_split, is_test=True
                )

                if decom_function_map:
                    if config_dict['training']:
                        function_map[func_name] = decom_function_map['stripped']
                        function_map[func_name]['bin_info'] = merge_code['bin_info']
                        function_map[func_name]['bin_name'] = merge_code['bin_name']
                        batch_results.update(function_map)
                    else:
                        function_map = decom_function_map
                        function_map['bin_info'] = merge_code['bin_info']
                        function_map['bin_name'] = merge_code['bin_name']
                        batch_results.append(function_map)

            except Exception:
                continue

    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(batch_results, f, ensure_ascii=False, indent=None)

    return temp_file


def process_function_batch(args) -> str:
    func_items: List[tuple] = args[0]
    config_dict = args[1]
    part_id = args[2]

    temp_file = os.path.join(config_dict['temp_dir'], f"part_{part_id:06d}.json")
    batch_results = defaultdict(dict) if config_dict['training'] else []

    for func_name, merge_code in func_items:
        try:
            function_map = defaultdict(dict)
            source_code = remove_comments(merge_code['unstripped']).replace('[MASK]', func_name)
            decom_code = remove_comments(merge_code['stripped']).replace('[MASK]', func_name)
            is_split = len(source_code.split('\n')) >= 17

            src_function_map = process_single_function_batch(
                source_code, 'unstripped', func_name, config_dict, is_split
            )
            decom_function_map = process_single_function_batch(
                decom_code, 'stripped', func_name, config_dict, is_split
            )

            if src_function_map and decom_function_map:
                function_map[func_name].update(src_function_map)
                function_map[func_name].update(decom_function_map)
                function_map[func_name]['bin_info'] = merge_code['bin_info']
                function_map[func_name]['bin_name'] = merge_code['bin_name']

                if config_dict['training']:
                    batch_results.update(function_map)
                else:
                    batch_results.append(function_map)

        except Exception:
            continue

    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(batch_results, f, ensure_ascii=False, indent=None)

    return temp_file


def batched_functions(iterable, n) -> Iterator[List]:
    it = iter(iterable.items())
    while True:
        chunk = list(itertools.islice(it, n))
        if not chunk:
            break
        yield chunk


def main(config):
    global TEMP_DIR

    TEMP_DIR = tempfile.mkdtemp(prefix="ast_processing_", dir=config.output_path)
    atexit.register(cleanup_temp_dir)

    with open(config.input_path, 'r', encoding='utf-8') as f:
        functions = json.load(f)


    config_dict = {
        'training': config.training,
        'temp_dir': TEMP_DIR,
    }

    max_workers = config.max_workers
    batch_size = config.batch_size
    tasks = [(batch, config_dict, i) for i, batch in enumerate(batched_functions(functions, batch_size))]

    temp_files = []
    with ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=get_context("spawn"),
        initializer=init_worker_temp_dir,
        initargs=(TEMP_DIR,)
    ) as executor:
        for temp_file in tqdm(executor.map(process_function_batch, tasks), total=len(tasks), desc="Processing Files"):
            temp_files.append(temp_file)

    final_data = {} if config.training else []
    for temp_file in temp_files:
        with open(temp_file, 'r', encoding='utf-8') as f:
            chunk = json.load(f)
            if config.training:
                final_data.update(chunk)
            else:
                final_data.extend(chunk)


    output_file = os.path.join(
        config.output_path,
        'code_ast_statements.json'
    )


    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Process the input training set data into statements.')
    parser.add_argument('-i',"--input_path", default="", type=str, required=True,
                        help="Path to the input data file.")
    parser.add_argument('-o', "--output_path", default="", type=str, required=True,
                        help="Directory where the processed output will be saved.")
    parser.add_argument("--training", action='store_true', default=True,
                        help="Flag indicating whether processing training set. (default: True).")
    parser.add_argument("--max_workers", default=16, type=int,
                        help="Maximum number of worker processes for multiprocessing (default: 16).")
    parser.add_argument("--batch_size", default=16, type=int,
                        help="Number of functions per batch for processing (default: 16).")

    args = parser.parse_args()
    main(args)