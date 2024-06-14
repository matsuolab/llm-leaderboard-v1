from config_singleton import WandbConfigSingleton
import subprocess
import time
import requests
import atexit
import tempfile

from utils import get_tokenizer_config


def start_vllm_server():
    instance = WandbConfigSingleton.get_instance()
    cfg = instance.config
    model_id = cfg.model.pretrained_model_name_or_path
    dtype = cfg.model.dtype

    def run_vllm_server(model_id: str, dtype: str):
        # set tokenizer_config
        tokenizer_config = get_tokenizer_config()
        cfg.update({"tokenizer_config": tokenizer_config})
        chat_template: str = cfg.tokenizer_config.get("chat_template")
        max_model_len = cfg.model.get("max_model_len", 2048)
        num_gpus = cfg.get("num_gpus", 1)

        with tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8') as temp_file:
            # chat_templateをファイルに書き込んでパスを取得
            temp_file.write(chat_template)
            chat_template_path = temp_file.name
            # サーバーを起動するためのコマンド
            command = [
                "python3", "-m", "vllm.entrypoints.openai.api_server",
                "--model", model_id, 
                "--dtype", dtype, 
                "--chat-template", chat_template_path,
                "--max-model-len", str(max_model_len),
                "--max-num-seqs", str(cfg.batch_size),
                "--tensor-parallel-size", str(num_gpus),
                "--seed", "42",
                "--uvicorn-log-level", "warning",
                "--disable-log-stats",
                "--disable-log-requests",
            ]
            # subprocessでサーバーをバックグラウンドで実行
            process = subprocess.Popen(command)

        # サーバーが起動するのを待つ
        time.sleep(10)

        # サーバーのプロセスを返す
        return process

    def health_check():
        url = "http://localhost:8000/health"
        while True:
            try:
                response = requests.get(url)
                if response.status_code == 200:
                    print("Health check passed!")
                    break
                else:
                    print(f"Health check failed with status code: {response.status_code}")
            except requests.ConnectionError:
                print("Failed to connect to the server. Retrying...")
            time.sleep(10)  # 待機してから再試行

    # サーバーを起動
    server_process = run_vllm_server(model_id, dtype)
    print("vLLM server is starting...")

    # スクリプト終了時にサーバーを終了する
    def cleanup():
        print("Terminating vLLM server...")
        server_process.terminate()
        server_process.wait()

    atexit.register(cleanup)

    # サーバーが完全に起動するのを待つ
    health_check()
