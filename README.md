# Hieronym

## Repository Contents

This repository contains the source code and scripts for **Hieronym**, a novel approach for renaming functions in stripped binaries. Hieronym is implemented using:

- **[Ghidra](https://github.com/NationalSecurityAgency/ghidra)** for decompilation
- **[ANTLR 4](https://www.antlr.org/)** for processing decompiled code
- **[LlamaFactory](https://github.com/hiyouga/LlamaFactory/tree/main)** for model fine-tuning
- **[vLLM](https://github.com/vllm-project/vllm)** for model inference

For comprehensive details, please refer to our paper.

## Environment Setup

### 1. Create a New Conda Environment
```bash
conda create -n hieronym python=3.10
```

### 2. Activate the Conda Environment
```bash
conda activate hieronym
```

### 3. Install Required Packages
```bash
pip install -r requirements.txt
```

### 4. Install Ghidra
Follow the official [Ghidra installation guide](https://github.com/NationalSecurityAgency/ghidra).

## Dataset

We adopt the same dataset used in the SOTA method **SymGen** (from the paper *“Beyond Classification: Inferring Function Names in Stripped Binaries via Domain Adapted LLMs”*). The original source code, binary files, and decompiled code can be downloaded [here](https://zenodo.org/records/15530083).

The dataset follows a three-level directory structure:

```
.
├── architecture
│   └── opt_level
│       └── project
```

- **architecture**: Includes four architectures: `x86_64`, `x86_32`, `arm`, and `mips`.
- **opt_level**: Contains optimization levels: `O0`, `O1`, `O2`, and `O3`.
- **project**: Contains 33 projects.


## Running Steps

All Python scripts are located in the `scripts/` directory.

```bash
cd scripts/
```

> **Note**: Most scripts support configurable arguments (e.g., `--output_dir`) to specify output locations. You can also modify default values directly in the scripts.

### 1. Decompile Binaries and Extract Calling Relationships

Scripts are located in `scripts/decompilation/`.

1. Set the `output_dir` in:
   - `decomp_for_unstripped.py`
   - `decomp_for_stripped.py`

2. Run parallel decompilation:
   ```bash
   python decompilation/parallel_decomp.py [-u | -s] \
       --binary_path YOUR_BINARIES_PATH \
       --ghidra_path YOUR_GHIDRA_PATH \
       --project_path YOUR_GHIDRA_PROJECT_PATH
   ```

   **Arguments**:
   - `-u, --unstripped`: Indicates that the binary is unstripped (contains debug symbols).
   - `-s, --stripped`: Indicates that the binary is stripped (lacks debug symbols).
   - `-b, --binary_path`: Path to binary file/folder.
   - `-g, --ghidra_path`: Path to Ghidra `analyzeHeadless`.
   - `-p, --project_path`: Path to Ghidra projects directory.

3. Set the `output_dir` in:
   - `cg_extractor_for_unstripped.py`
   - `cg_extractor_for_stripped.py`

4. Extract calling relationships:
   ```bash
   python decompilation/parallel_for_calling.py [-u | -s] \
       --binary_path YOUR_BINARIES_PATH \
       --ghidra_path YOUR_GHIDRA_PATH \
       --project_path YOUR_GHIDRA_PROJECT_PATH
   ```

   **Arguments**: Same as step 2.

### 2. Data Processing

Scripts are located in `scripts/data_processing/`.

#### 2.1 Combine Stripped/Unstripped Functions and Calling Relationships

**For dataset generation (`-d`)**:
```bash
python data_processing/process_decompiled_data.py -d \
    --unstripped_bin_path YOUR_UNSTRIPPED_DECOMP_DATA_PATH \
    --stripped_bin_path YOUR_STRIPPED_DECOMP_DATA_PATH \
    --unstripped_call_path YOUR_UNSTRIPPED_CALL_DATA_PATH \
    --stripped_call_path YOUR_STRIPPED_CALL_DATA_PATH \
    --output_dir YOUR_OUTPUT_DIR
```

**For prediction on stripped binaries (`-p`)**:
```bash
python data_processing/process_decompiled_data.py -p \
    --stripped_path YOUR_STRIPPED_DECOMP_DATA_PATH \
    --stripped_call_path YOUR_STRIPPED_CALL_DATA_PATH \
    --output_dir YOUR_OUTPUT_DIR
```

**Arguments**:
- `-d, --dataset`: Indicates the purpose of generating a new dataset for training and testing purposes.
- `-p, --prediction`: Indicates the purpose of prediction of a new stripped binary.
- `-ub, --unstripped_bin_path`: Path to JSON files containing decompiled unstripped binaries.
- `-sb, --stripped_bin_path`: Path to JSON files containing decompiled stripped binaries.
- `-uc, --unstripped_call_path`: Path to JSON files containing calling context of unstripped binaries.
- `-sc, --stripped_call_path`: Path to JSON files containing calling context of stripped binaries.
- `-o, --output_dir`: Directory to save the output files.

#### 2.2 Combine Calling Function Names with Their Code
```bash
python data_processing/relate_cg_func_name_with_code.py \
    --input YOUR_COMBINED_DATA_PATH \
    --output YOUR_OUTPUT_DIR
```

**Arguments**:
- `-i, --input`: Path to JSON files integrating stripped functions, unstripped functions, and their call details (which is the output path of the 2.1).
- `-o, --output`: Output directory.

#### 2.3 Extract Task-Related Information from Stripped Binaries
```bash
python data_processing/binary_information_extract.py \
    --input_dir YOUR_STRIPPED_BINARIES_DIR \
    --output_path YOUR_OUTPUT_PATH
```

**Arguments**:
- `-i, --input_dir`: Directory of stripped binaries (usually `opt_level` folder).
- `-o, --output_path`: Output file path.

#### 2.4 Generate Global Binary Context

This step requires vLLM. First, launch the vLLM server using Qwen3-Coder. Update `tensor-parallel-size` in `qwen3-server.yaml` (requires 4 or 8 GPUs).

```bash
vllm serve --config qwen3-server.yaml
```

Then update `VLLM_API_URL` and `VLLM_MODEL_NAME` in `data_processing/generate_global_binary_context.py`.

```bash
python data_processing/generate_global_binary_context.py \
    --input_file YOUR_EXTRACTED_STRINGS_PATH \
    --output_file YOUR_OUTPUT_PATH
```

**Arguments**:
- `-i, --input_path`: Path to the JSON file containing the extracted binary information.
- `-o, --output_dir`: Output path for global binary context.

### 3. Divide Dataset

Split data into training/validation/test sets. Duplicate entries are removed from the dataset based on the function name and function body. For more details, please refer to our paper.

```bash
python data_processing/divide_dataset.py \
    --binary_context_path YOUR_GLOBAL_BINARY_CONTEXT_PATH \
    --input_dir YOUR_PROCESSED_DECOMP_DIR \
    --output_dir YOUR_OUTPUT_DIR
```

**Arguments**:
- `-b, --binary_context_path`: Path to the JSON file containing global binary context.
- `-i, --input_dir`: Directory with combined decompiled code.
- `-o, --output_dir`: Directory to save the divided dataset.

#### 3.1 Generate Statements for Training Set
```bash
python data_processing/generate_decompiled_statements.py \
    --input_path YOUR_TRAINING_SET_PATH \
    --output_path YOUR_OUTPUT_DIR
```

**Arguments**:
- `-i, --input_path`: Path of training set.
- `-o, --output_path`: Directory to save the output.

#### 3.2 Align Unstripped and Stripped Code Snippets
```bash
python data_processing/unstriped_striped_align.py \
    --input YOUR_TRAINING_SET_WITH_STATEMENTS_PATH
```

**Arguments**:
- `--input`:  Path of training set which contains statements.

### 4. Generate Summaries

First, launch the vLLM server (as in step 2.4).

#### 4.1 Generate Code Snippet Summaries

Update `VLLM_API_URL` and `VLLM_MODEL_NAME` in `summary_generation/generate_decompiled_code_snippets_summary.py`.

```bash
python summary_generation/generate_decompiled_code_snippets_summary.py \
    --input_file YOUR_TRAINING_SET_WITH_STATEMENTS_PATH \
    --mapping_file YOUR_ALIGN_MAPPING_FILE \
    --output_dir YOUR_OUTPUT_FILE_DIR
```

**Arguments**:
- `-i, --input_file`: Path to the training set containing decompiled functions (with statements).
- `-m, --mapping_file`: Path to the mapping file between stripped/unstripped code snippets.
- `-o, --output_dir`: Directory to save the output JSON file.

#### 4.2 Generate Calling Context Summaries

Update `VLLM_API_URL` and `VLLM_MODEL_NAME` in `summary_generation/generate_calling_context_summary.py`.

```bash
python summary_generation/generate_calling_context_summary.py \
    --input_file YOUR_SET_PATH \
    --output_dir YOUR_OUTPUT_DIR \
    --type YOUR_SET_TYPE
```

**Arguments**:
- `-i, --input_file`: Path to training/validation/test set.
- `-o, --output_dir`: Directory to save the output JSON file.
- `--type`: Dataset type: `training`, `test`, or `validation`.

#### 4.3 Format Data for Model Input

**For training dataset**:
```bash
python summary_generation/format_data.py \
    --data_path YOUR_TRAINING_SET_FILE \
    --summary_data YOUR_SUMMARY_DATA_FILE \
    --arch YOUR_DATA_ARCHITECTURE \
    --opt_list OPTIMIZATION_LEVELS_INCLUDED \
    --output_dir YOUR_OUTPUT_DIR
```

**For validation dataset**:
```bash
python summary_generation/format_data.py \
    --data_path YOUR_VALIDATION_SET_FILE \
    --arch YOUR_DATA_ARCHITECTURE \
    --opt_list OPTIMIZATION_LEVELS_INCLUDED \
    --output_dir YOUR_OUTPUT_DIR
```

**For test dataset**:
```bash
python summary_generation/format_data.py \
    --data_path YOUR_TEST_SET_FILE \
    --arch YOUR_DATA_ARCHITECTURE \
    --opt OPTIMIZATION_LEVEL_INCLUDED \
    --output_dir YOUR_OUTPUT_DIR \
    --prediction
```

**Arguments**:
- `-d, --data_path`: Directory to the JSON file containing the combined data for training/validation/test set.
- `-s, --summary_data`: Directory to the JSON file containing code snippets summaries (training only).
- `--arch`: Architecture type.
- `--opt_list`: List of optimization levels (training/validation).
- `--opt`: Specific optimization level (test).
- `-o, --output_dir`: Directory to save the format dataset.

### 5. Fine-tune and Predict

Based on **LlamaFactory**. We provide `LlamaFactory.zip` in `model_training/`.

#### 5.1 Fine-tune

1. Install LlamaFactory.
2. Add dataset configuration to `LlamaFactory/data/dataset_info.json`. See example in `data_info.json`.
3. Place `fine-tuning.yaml` in `LlamaFactory/examples/train_lora/`.
4. Ensure the `dataset` field of `fine-tuning.yaml` matches your `rename_data` entry in `dataset_info.json`.
5. The base model used for this training session is CodeLlama-34b-Instruct-hf.

```bash
llamafactory-cli train examples/train_lora/fine_tuning.yaml
```

> Pre-trained LoRA weights are available in `lora_weights/`.

#### 5.2 Predict

Based on **vLLM**. First, launch the vLLM service with fine-tuned LoRA modules. Update `MODEL_NAME` and `LORA_PATH` in `model_training/start_vllm_server.py`.

```bash
CUDA_VISIBLE_DEVICES=0 python model_training/start_vllm_server.py
```

Then generate predictions:

```bash
CUDA_VISIBLE_DEVICES=0 python model_training/predict.py \
    --input_file YOUR_TEST_SET_FILE \
    --output_file YOUR_OUTPUT_PATH
```

**Arguments**:
- `--input_file`: Path to the test set JSON file.
- `--output_file`: Output path for predictions.

### 6. Evaluation

Scripts are located in `scripts/evaluation/`.

#### 6.1 Token-Level Evaluation

First, launch the vLLM server (as in step 2.4).
Then, extract the function name from the results and tokenize them into tokens:
```bash
python evaluation/name_tokenization.py \
    --input_file YOUR_PRED_RESULTS_FILE \
    --output_dir YOUR_OUTPUT_DIR
```
**Arguments:**
- `-i, --input_file`: Path to the input file containing predicted function names and ground truth.
- `-o, --output_dir`: Directory to save the processed evaluation results.


Calculate precision, recall, and F1 score:

```bash
python evaluation/calculate_score.py \
    --input_file YOUR_PROCESSED_RESULTS_FILE
```

**Arguments**:
- `-i, --input_file`: Path to the processed evaluation results file(.txt).

#### 6.2 Name-Level Evaluation

To construct the input for ChatGPT, we first preprocess the test data and the prediction results generated by the model.

```bash
python evaluation/process_data_for_chatgpt.py \
    --test_file YOUR_TEST_SET_FILE \
    --predict_file YOUR_PREDICTION_RESULTS_FILE \
    --output_file YOUR_OUTPUT_PATH
```
**Arguments:**
- `-t, --test_file`: Path to the test set, used to obtain the contextual information required for ChatGPT queries.
- `-p, --predict_file`: Path to the input file containing predicted function names and ground truth.
- `-o, --output_file`: Path to save the processed results.

Uses ChatGPT and Qwen3-embedding for correctness assessment.

```bash
python evaluation/gpt_analysis.py \
    --input_file YOUR_PREDICTION_RESULTS_FILE \
    --output_file YOUR_OUTPUT_PATH
```

```bash
python evaluation/qwen_analysis.py \
    --input_file YOUR_PREDICTION_RESULTS_FILE \
    --output_file YOUR_OUTPUT_PATH
```
**Arguments:**
- `-i, --input_file`: Path to the input file containing predicted function names and ground truth.
- `-o, --output_file`: Path to save the evaluation results.

Calculate accuracy:

```bash
python evaluation/calculate_accuracy.py \
    --input_gpt_file YOUR_GPT_EVALUATION_RESULTS \
    --input_qwen_file YOUR_QWEN_EVALUATION_RESULTS
```

**Arguments**:
- `--input_gpt_file`: Path to the results evaluated by ChatGPT.
- `--input_qwen_file`: Path to the results evaluated by Qwen3-embedding.
