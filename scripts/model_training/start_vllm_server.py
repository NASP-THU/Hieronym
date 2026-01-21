import os
import subprocess
import signal
import sys
import time
import requests
from threading import Thread

# ================== Configuration ==================
MODEL_NAME = "/path/to/base_model"
LORA_PATH = "saves/codellama-34b/lora/sft" # path of the fine-tuned lora weights
HOST = "127.0.0.1"
PORT = 8081

GPU_MEMORY_UTILIZATION = 0.90
MAX_MODEL_LEN = 16000
DTYPE = "auto"
TP_SIZE = 1                     # Tensor Parallelism (set >1 for multi-GPU)
CHAT_TEMPLATE = False

LOG_FILE = "lora_vllm_server.log"
# ===================================================

vllm_process = None

def check_vllm_installed():
    """Check vLLM installation"""
    try:
        import vllm
        print(f"vLLM is installed, version: {vllm.__version__}")
    except ImportError:
        print("vLLM is not installed, installing now...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "vllm>=0.7.0"])

def start_vllm_server():
    """Start vLLM server"""
    global vllm_process
    
    if TP_SIZE > 1:
        import multiprocessing as mp
        mp.set_start_method('spawn', force=True)
        print(f"Set multiprocessing start method to 'spawn' for TP_SIZE={TP_SIZE}")

    cmd = [
        sys.executable, "-m", "vllm.entrypoints.openai.api_server",
        "--host", HOST,
        "--port", str(PORT),
        "--model", MODEL_NAME,
        "--enable-lora",
        f"--lora-modules=lora={LORA_PATH}",
        "--dtype", DTYPE,
        "--max-model-len", str(MAX_MODEL_LEN),
        "--gpu-memory-utilization", str(GPU_MEMORY_UTILIZATION),
        "--tensor-parallel-size", str(TP_SIZE),
        "--disable-log-requests",
        "--served-model-name", "lora_model"
    ]

    if CHAT_TEMPLATE:
        cmd += ["--chat-template", "default"]

    print(f"Starting vLLM server...")
    print(f"   Model: {MODEL_NAME}")
    print(f"   Address: http://{HOST}:{PORT}/v1")
    print(f"   Log: {os.path.abspath(LOG_FILE)}")

    log_file = open(LOG_FILE, 'w', encoding='utf-8')
    log_file.write(f"=== vLLM Server Log ({time.ctime()}) ===\n")
    log_file.flush()

    try:
        vllm_process = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=log_file,
            bufsize=0
        )
    except Exception as e:
        print(f"Failed to start: {e}")
        sys.exit(1)

    if not wait_for_server_ready():
        print("Server startup timed out or failed, please check the log file")
        sys.exit(1)

    print(f"vLLM server is ready!")

def wait_for_server_ready(timeout=120):
    """Wait for server to be ready"""
    start_time = time.time()
    url = f"http://{HOST}:{PORT}/v1/models"
    while time.time() - start_time < timeout:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                model_info = resp.json()
                print(f"Model loaded successfully: {model_info['data'][0]['id']}")
                return True
        except requests.RequestException:
            time.sleep(2)
    return False

def signal_handler(signum, frame):
    """Handle termination signals"""
    print(f"\nReceived signal {signum}, shutting down vLLM server...")
    if vllm_process:
        vllm_process.terminate()
        try:
            vllm_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            vllm_process.kill()
    print("vLLM server has been shut down")
    sys.exit(0)

def monitor_process():
    """Monitor subprocess status"""
    if vllm_process:
        exit_code = vllm_process.wait()
        print(f"vLLM process exited with return code: {exit_code}")
        print(f"Please check the log file: {os.path.abspath(LOG_FILE)}")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    check_vllm_installed()
    start_vllm_server()

    monitor_thread = Thread(target=monitor_process, daemon=True)
    monitor_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)