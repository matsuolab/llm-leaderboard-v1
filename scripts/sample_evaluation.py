import json
import time
from pathlib import Path

import wandb
from langchain.prompts import BasePromptTemplate
from tqdm import tqdm
import pandas as pd
from toolz import pipe

from config_singleton import WandbConfigSingleton
from utils import (
    get_evaluation_prompt,
    get_few_shot_samples,
    Sample,
    normalize,
    metrics_func_dict,
    text_formatter,
)

# 実装はSaaSだけでなく、dedicated cloudでも動くように、OpenAIだけでなく、Azure OpenAIでも動くように心がけてください


def sample_evaluate():
    # Retrieve the instance from WandbConfigSingleton and load the W&B run and configuration
    instance = WandbConfigSingleton.get_instance()
    run = instance.run
    cfg = instance.config
    llm = instance.llm

    # データセットを取得しましょう
    artifact_dir = run.use_artifact(
        cfg.sample_dataset.artifacts_path, type="dataset"
    ).download()

    target_datasets = [
        "chabsa",
        "mawps",
        "niilc",
    ]

    for target_dataset in target_datasets:
        # 評価タスクを実行しましょう
        output_table_dict = {}
        for eval_type in ("test", "dev"):
            eval_matainfo = {
                "run_name": run.name,
                "model_name": cfg.model.pretrained_model_name_or_path,
                "target_dataset": target_dataset,
                "num_few_shots": cfg[target_dataset].num_few_shots,
                "eval_type": eval_type,
            }

            target_dataset_path = (
                Path(artifact_dir)
                / eval_type
                / target_dataset
                / f"{target_dataset}.json"
            )
            if not target_dataset_path.exists():
                print(f"skip {target_dataset} because it is not found in {artifact_dir}")
                continue
            with target_dataset_path.open(encoding="utf-8") as f:
                target_data = json.load(f)

            # カスタムプロンプトがあれば設定
            if "custom_prompt_template" in cfg:
                custom_prompt_template = cfg.custom_prompt_template
            else:
                custom_prompt_template = None

            if "custom_fewshots_template" in cfg:
                custom_fewshots_template = cfg.custom_fewshots_template
            else:
                custom_fewshots_template = None

            # fewshotsを取得
            few_shots: list[Sample] = get_few_shot_samples(
                target_dataset_path=target_dataset_path,
                num_few_shots=cfg[target_dataset].num_few_shots,
            )

            # prompt取得
            prompt: BasePromptTemplate = get_evaluation_prompt(
                instruction=target_data["instruction"],
                few_shots=few_shots,
                language=cfg.sample_dataset.language,
                custom_prompt_template=custom_prompt_template,
                custom_fewshots_template=custom_prompt_template,
            )

            # 評価サンプル数
            test_max_num_samples = 100
            val_max_num_samples = 10
            if cfg.testmode:
                test_max_num_samples = 2
                val_max_num_samples = 2
            if eval_type == "test":
                num_samples = test_max_num_samples
            elif eval_type == "dev":
                num_samples = val_max_num_samples
            samples = target_data["samples"][:num_samples]

            # llm pipline
            llm.max_tokens = target_data["output_length"]
            chain = prompt | llm

            evaluation_results = []
            for idx, sample in tqdm(enumerate(samples)):
                # generation
                input_data = {"input": sample["input"]}
                start_time = time.time()
                output = chain.invoke(input_data)
                end_time = time.time()
                latency = end_time - start_time
                prompt = prompt.format(**input_data)

                # scoring
                y_pred: str = pipe(
                    output,
                    lambda x: text_formatter(x, target_dataset),
                    lambda x: x.split("\n\n")[0],
                    normalize,
                )
                y_true: str = pipe(sample["output"], normalize)
                metrics: list[str] = target_data["metrics"][0]
                metrics_func: callable = metrics_func_dict[metrics]
                score = metrics_func(y_pred, y_true)

                # matome
                evaluation_results.append(
                    {
                        **eval_matainfo,
                        "index": idx,
                        "input": sample["input"],
                        "output": y_pred,
                        "expected_output": y_true,
                        "prompt": prompt,
                        "metrics": metrics,
                        "score": score,
                        "latency": latency,
                    }
                )

            # tmp save
            table_name = target_dataset + "_output_table"
            if eval_type == "dev":
                table_name += "_dev"
            output_table_dict[table_name] = evaluation_results

        # output table
        wandb.log(
            {
                table_name: wandb.Table(data=pd.DataFrame(v))
                for table_name, v in output_table_dict.items()
            }
        )
