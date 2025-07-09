import re

class BackslashReplacer:
    def __call__(self, match_obj):
        digits = match_obj.group(1)
        return "_" + digits

def process_line_for_escapes(line_str):
    def transform_quoted_content(match_obj_outer):
        quoted_inner_content = match_obj_outer.group(1)
        replacer_instance = BackslashReplacer()
        transformed_inner_content = re.sub(r"\\(\d+)", replacer_instance, quoted_inner_content)
        return f'"{transformed_inner_content}"'
    return re.sub(r'"([^"]*)"', transform_quoted_content, line_str)

def _format_constraints_and_get_map(constraints_str_list):
    if not constraints_str_list:
        return "[]", {}
    formatted = ",\n".join([f"    {c}" for c in constraints_str_list])
    formatted = re.sub('f16', 'f32', formatted)
    formatted = re.sub('\(([a-z_]+):([^)]+)\)', r'{\1:\2}', formatted)
    formatted = formatted.replace('})',')}')

    name_mapping = {}
    output_parts = []
    last_end = 0

    for match in re.finditer(r'["\(\[\]]+(?P<word>(?!bool)[a-zA-Z.]+)[,")\]](?!_)', formatted):
        original_name = match.group(1)
        start, end = match.span()
        output_parts.append(formatted[last_end:start])

        new_name_core = f'{original_name}_{get_number()}'

        if original_name not in name_mapping:
            name_mapping[original_name] = new_name_core
        
        output_parts.append(f'"{new_name_core}"')
        last_end = end

    return f"[\n{formatted}\n    ]", name_mapping

# Global counter for unique numbering of type parameters
counter = 0
def get_number():
    global counter
    counter += 1
    return counter


def _format_typarams_and_get_map(typarams_str_input: str):
    # Handle empty or "mempty" typarams
    if not typarams_str_input or typarams_str_input == "mempty":
        return "[]", {}
    name_mapping = {}
    
    current_str = typarams_str_input.replace('"(\\', '(').replace('\\"', '"').replace(')"', ')')
    
    output_parts = []
    last_end = 0
    
    for match in re.finditer(r'"([a-z])"', current_str):
        original_name = match.group(1)
        start, end = match.span()
        output_parts.append(current_str[last_end:start])
        
        new_name_core = f"{original_name}_{get_number()}"
        
        if original_name not in name_mapping:
            name_mapping[original_name] = new_name_core
        
        output_parts.append(f'"{new_name_core}"')
        last_end = end
        
    output_parts.append(current_str[last_end:])
    processed_typarams_str = "".join(output_parts)
    
    return processed_typarams_str, name_mapping


def _apply_renaming_to_constraints(constraints_list: list[str], name_map: dict[str, str]) -> list[str]:
    if not name_map or len(name_map.keys()) == 0:
        return constraints_list
    updated_constraints = []

    sorted_original_names = sorted(name_map.keys(), key=len, reverse=True)
    for constr_str in constraints_list:
        current_constr = constr_str
        for original_name in sorted_original_names:
            new_name = name_map[original_name]
            current_constr = re.sub(r'\b' + re.escape(original_name) + r'\b', new_name, current_constr)
        updated_constraints.append(current_constr)
    return updated_constraints

def _parse_single_problem_block_to_haskell_tuple_str(problem_block_text: str, min_n_cons: int, top_n_cons: int) -> str:
    input_lines = problem_block_text.splitlines()
    
    actual_content_lines = []
    if input_lines:
        first_line_content = input_lines[0].strip()
        if first_line_content == "# TySolve.solve":
            actual_content_lines = input_lines[1:]
        else:
            actual_content_lines = input_lines
    constraints_str_list = []

    typarams_str_list = "mempty" 
    tyvars_str_list = "[]"
    current_section = None
    
    for original_line in actual_content_lines:
        line = original_line.strip()
        line = process_line_for_escapes(line) 
        
        if line.startswith("## "):
            current_section = line[3:].strip().lower()
            continue
        if current_section == "constraints":
            if line:
                constraints_str_list.append(line)
        elif current_section == "typarams":
            if line:
                typarams_str_list = line
        elif current_section == "tyvars":
            if line:
                tyvars_str_list = line
    
    if min_n_cons > len(constraints_str_list) and top_n_cons <= 0:
        return ""
    
    global counter
    counter = 0 # Reset global counter for each block's type parameter renaming

    formatted_typarams_for_haskell, typaram_name_map = _format_typarams_and_get_map(typarams_str_list)

    updated_constraints_list = _apply_renaming_to_constraints(constraints_str_list, typaram_name_map)
    _, constraints_name_map = _format_constraints_and_get_map(updated_constraints_list)
    final_constraints_list = _apply_renaming_to_constraints(updated_constraints_list, constraints_name_map)
    formatted_constraints_for_haskell, _ = _format_constraints_and_get_map(final_constraints_list)

    if tyvars_str_list.strip() == "[]":
        return ""
    
    tyvars_str_list = tyvars_str_list.replace('fromList','M.fromList').replace('(Name ','(')

    haskell_tuple_string = f"  (    \n    {formatted_constraints_for_haskell},\n    M.fromList {formatted_typarams_for_haskell},\n    M.fromList {tyvars_str_list}\n  )"
    
    return (haskell_tuple_string, len(constraints_str_list))


def parse_log_to_haskell_tuples_list(full_log_text: str, min_n_cons: int, top_n_cons: int) -> list[str]:
    if not full_log_text.strip():
        return []

    potential_blocks = re.split(r'\n*(?=# TySolve\.solve)', full_log_text)
    potential_blocks = potential_blocks[1664:]

    parsed_haskell_tuples = []
    for block_text_content in potential_blocks:
        stripped_block = block_text_content.strip()
        
        if stripped_block:
            tuple_str = _parse_single_problem_block_to_haskell_tuple_str(stripped_block, min_n_cons, top_n_cons)
            if tuple_str == "": continue
            parsed_haskell_tuples.append(tuple_str)
    if top_n_cons > 0:
        sorted_haskell_tuples = sorted(parsed_haskell_tuples, key=lambda item: item[1], reverse=True)
        parsed_haskell_tuples = sorted_haskell_tuples[:top_n_cons]
    
    return parsed_haskell_tuples

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        input_filename = sys.argv[1]
        try:
            with open(input_filename, 'r', encoding='utf-8') as infile:
                log_data = infile.read()
            
            print(f"--- Input log from {input_filename} ---")
            
            haskell_expr_list = parse_log_to_haskell_tuples_list(log_data)
            
            if haskell_expr_list:
                print(f"\n--- Parsed List of {len(haskell_expr_list)} Haskell Tuple String(s) ---")
                print("[\n" + ",\n\n".join(haskell_expr_list) + "\n]")
            else:
                print("\n--- Log parser returned no data blocks (empty list). ---")

        except FileNotFoundError:
            print(f"Error: Test input file '{input_filename}' not found.")
        except Exception as e:
            print(f"Error processing test input file: {e}")
            raise
    else:
        print("To test futhark_log_parser.py: python futhark_log_parser.py <path_to_log_file>")

