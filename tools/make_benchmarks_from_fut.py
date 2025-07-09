#!/usr/bin/env python3

import argparse
import os
import pathlib
import re
import subprocess
import sys
import traceback

try:
    import futhark_log_parser 
except ImportError:
    print("Error: futhark_log_parser.py not found. Make sure it's in the same directory or Python path.", file=sys.stderr)
    sys.exit(1)

HS_MODULE_TEMPLATE = """module Generated.{module_name} (benchmarkDataList) where

import Futhark.Util.Loc (Loc(NoLoc))
import Data.Map qualified as M
import Language.Futhark.Syntax
import Language.Futhark.SyntaxTests ()
import Language.Futhark.TypeChecker.Constraints
  ( CtTy (..),
    Reason (..),
    TyParams,
    TyVarInfo (..),
    TyVars
  )

(~) :: TypeBase () NoUniqueness -> TypeBase () NoUniqueness -> CtTy ()
t1 ~ t2 = CtEq (Reason mempty) t1 t2

type BenchmarkCaseData = ([CtTy ()], TyParams, TyVars ())

benchmarkDataList :: [BenchmarkCaseData]
benchmarkDataList =
  [ {comma_separated_data_tuple_strings}
  ]
"""

HS_MASTER_LIST_TEMPLATE_HEADER = """module Generated.{module_name} (allFutBenchmarkCases) where

import Language.Futhark.TypeChecker.Constraints (CtTy, TyParams, TyVars)
import Language.Futhark.Syntax ()

-- Imports for each generated module:
{imports_block}

type BenchmarkCaseData = ([CtTy ()], TyParams, TyVars ())

allFutBenchmarkCases :: [(String, BenchmarkCaseData)]
allFutBenchmarkCases =
  [ {entries_block}
  ]
"""

def sanitize_for_haskell_module_component(name: str) -> str:
    name = re.sub(r'[^a-zA-Z0-9_]', '', name)
    if not name or name[0].isdigit() or name[0] == '_':
        name = "M" + name
    return name[0].upper() + name[1:] if name else "DefaultModuleComponent"


def fut_filepath_to_haskell_module_qualifier(relative_fut_path: str, output_base_dir_name: str) -> str:
    path = pathlib.Path(relative_fut_path)
    components = [output_base_dir_name] + \
                 [sanitize_for_haskell_module_component(part) for part in path.parts[:-1]] + \
                 [sanitize_for_haskell_module_component(path.stem)]
    return ".".join(components)

def main():
    parser = argparse.ArgumentParser(description="Generate Haskell benchmark modules from .fut files.")
    parser.add_argument("input_dir", type=pathlib.Path, help="Directory with .fut files to convert.")
    parser.add_argument("output_file", type=pathlib.Path, help="Master Haskell file listing all generated benchmarks.")
    parser.add_argument("output_dir", type=pathlib.Path, help="Directory to place generated .hs benchmark files.")
    parser.add_argument("--exclude-dirs", nargs="+", default=[], help="Directory names to exclude from traversal.")
    parser.add_argument("--exclude-files", nargs="+", default=[], help="File names to exclude from conversion.")
    parser.add_argument("--top-n-cons", type=int, default=-1, help="Only convert the top n problems with the most constraints.\nWill overrule --min-n-cons, if set.")
    parser.add_argument("--min-n-cons", type=int, default=-1, help="A problem must have a minimum of n constraints to be converted.\nWill be overruled by --top-n-cons, if set.")
    parser.add_argument("--stop-on-err", action="store_true", help="Stop execution if any file conversion fails.")

    args = parser.parse_args()

    if not args.input_dir.is_dir():
        print(f"Error: Input directory '{args.input_dir}' does not exist or is not a directory.", file=sys.stderr)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    
    master_module_name_parts = args.output_file.stem.split('.')
    if not master_module_name_parts:
        print(f"Error: Could not determine base module name from OUTPUT_FILE '{args.output_file.name}'", file=sys.stderr)
        sys.exit(1)
    generated_base_module_name = master_module_name_parts[0]

    actual_generated_src_dir = args.output_dir / generated_base_module_name
    actual_generated_src_dir.mkdir(parents=True, exist_ok=True)

    processed_files_info = [] 

    print(f"Starting benchmark generation from: {args.input_dir}")
    print(f"Outputting individual modules to: {args.output_dir}")
    print(f"Outputting master list to: {args.output_file}")
    if args.exclude_dirs: print(f"Excluding directories: {args.exclude_dirs}")
    if args.exclude_files: print(f"Excluding files: {args.exclude_files}")

    for root, dirs, files in os.walk(args.input_dir, topdown=True):
        dirs[:] = [d for d in dirs if d not in args.exclude_dirs]

        for filename in files:
            if not filename.endswith(".fut"):
                continue
            if filename in args.exclude_files:
                print(f"Skipping excluded file: {filename}")
                continue

            fut_file_path = pathlib.Path(root) / filename
            relative_fut_path = fut_file_path.relative_to(args.input_dir)
            print(f"\nProcessing: {relative_fut_path}")

            futhark_log_output = "" 
            haskell_data_tuple_strings_list = None

            # 1. Run FUTHARK_LOG_TYSOLVE=0 futhark check <FILE>
            env = os.environ.copy()
            env["FUTHARK_LOG_TYSOLVE"] = "0"
            try:
                process = subprocess.run(
                    ["futhark", "check", str(fut_file_path)],
                    capture_output=True, text=True, check=False, env=env, timeout=300
                )
                futhark_log_output = process.stderr 
                futhark_stdout_for_error_check = process.stdout

                if process.returncode != 0:
                    print(f"  Error: 'futhark check {relative_fut_path}' failed (exit code {process.returncode}).", file=sys.stderr)
                    if not futhark_stdout_for_error_check.strip() and not futhark_log_output.strip():
                         print(f"  (futhark check produced no output on stdout or stderr)", file=sys.stderr)
                    if args.stop_on_err: sys.exit(1)
                    continue 

                if not futhark_log_output.strip() and process.returncode == 0:
                    print(f"  Warning: 'futhark check {relative_fut_path}' succeeded but produced no log output on stderr. Skipping.", file=sys.stderr)
                    continue

            except Exception as e:
                print(f"  Error running 'futhark check {relative_fut_path}': {e}", file=sys.stderr)
                traceback.print_exc()
                if args.stop_on_err: sys.exit(1)
                continue
            
            # 2. Parse the output
            try:
                haskell_data_tuple_strings_list = futhark_log_parser.parse_log_to_haskell_tuples_list(futhark_log_output, args.min_n_cons, args.top_n_cons)

                if haskell_data_tuple_strings_list is None or not haskell_data_tuple_strings_list:
                    print(f"  Warning: Log parser returned no data blocks for {relative_fut_path}. Log output might be empty or malformed or --min-n-cons/--top-n-cons have sorted all problems away. Skipping.", file=sys.stderr)
                    continue
            except Exception as e:
                print(f"  Error parsing log for {relative_fut_path} with futhark_log_parser: {e}", file=sys.stderr)
                traceback.print_exc()
                if args.stop_on_err: sys.exit(1)
                continue
            
            # 3. Determine Haskell module name and path
            hs_module_qualifier = fut_filepath_to_haskell_module_qualifier(relative_fut_path, generated_base_module_name)
            module_path_parts = hs_module_qualifier.split('.')
            hs_file_relative_path = pathlib.Path(*module_path_parts).with_suffix(".hs")
            hs_file_abs_path = args.output_dir / hs_file_relative_path
            hs_file_abs_path.parent.mkdir(parents=True, exist_ok=True)

            # 4. Write the individual .hs file
            haskell_data_sz_list = [item[1] for item in haskell_data_tuple_strings_list]
            haskell_data_tuple_strings_list = [item[0] for item in haskell_data_tuple_strings_list]
            comma_separated_data_tuples = ",\n\n".join(haskell_data_tuple_strings_list)
            
            module_content = HS_MODULE_TEMPLATE.format(
                module_name=hs_module_qualifier,
                comma_separated_data_tuple_strings=comma_separated_data_tuples
            )
            try:
                with open(hs_file_abs_path, "w", encoding="utf-8") as f:
                    f.write(module_content)
                print(f"  Generated: {hs_file_abs_path} (with {len(haskell_data_tuple_strings_list)} data block(s))")
            except Exception as e:
                print(f"  Error writing Haskell module {hs_file_abs_path}: {e}", file=sys.stderr)
                traceback.print_exc()
                if args.stop_on_err: sys.exit(1)
                continue

            processed_files_info.append((str(relative_fut_path), hs_module_qualifier, len(haskell_data_tuple_strings_list), haskell_data_sz_list))

    # 5. Create the master list file
    if not processed_files_info:
        print("\nNo .fut files were successfully processed. Master list will be empty.")
    
    imports_block_lines = []
    entries_block_lines = []
    processed_files_info.sort(key=lambda x: x[0]) 

    for fut_file_display_name, hs_module_qualifier, num_blocks, num_cons in processed_files_info:
        alias_parts = hs_module_qualifier.split('.')
        alias = "".join(part for part in alias_parts if part) 
        imports_block_lines.append(f"import qualified Generated.{hs_module_qualifier} as {alias}")
        
        if num_blocks == 0: # Should not be possible
            continue

        for i in range(num_blocks):
            bench_name = f"{fut_file_display_name} (Block {i+1}/{num_blocks}) (Cons: {num_cons[i]})" if num_blocks > 1 else f"{fut_file_display_name} (Cons: {num_cons[i]})"
            entries_block_lines.append(f"  (\"{bench_name}\", {alias}.benchmarkDataList !! {i})")

    imports_block = "\n".join(imports_block_lines)
    entries_block = ",\n".join(entries_block_lines) if entries_block_lines else ""
    
    master_list_module_name = ".".join(pathlib.Path(args.output_file.stem).parts)

    master_list_content = HS_MASTER_LIST_TEMPLATE_HEADER.format(
        module_name=master_list_module_name,
        imports_block=imports_block,
        entries_block=entries_block
    )
    try:
        with open(args.output_file, "w", encoding="utf-8") as f:
            f.write(master_list_content)
        print(f"\nGenerated master list: {args.output_file}")
    except Exception as e:
        print(f"Error writing master list file {args.output_file}: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    print("\nBenchmark generation complete.")

if __name__ == "__main__":
    main()

