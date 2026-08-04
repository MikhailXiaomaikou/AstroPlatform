#!/usr/bin/env python3
"""Build a blinded 12-pair expert review pack from the completed v0.2 matrix.

The blinded pack and private answer key default to ignored `.local/` paths.
No model name, condition, routing state, or tool metadata enters the review pack.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SAMPLES = REPO_ROOT / ".local/standard-astro-v02/evaluation_samples.jsonl"
DEFAULT_PACK = REPO_ROOT / ".local/standard-astro-v02/expert_blind_pack.zh-CN.md"
DEFAULT_KEY = REPO_ROOT / ".local/standard-astro-v02/expert_blind_answer_key.json"
MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "claude-fable-5",
)
SELECTION = {
    "gpt-5.6-sol": (
        ("V02_01_desi_dr2_ratio", 1),
        ("V02_04_act_dr6_ns", 2),
        ("V02_07_desi_dr2_ede_gap", 3),
    ),
    "gpt-5.6-terra": (
        ("V02_02_desi_dr2_correlation", 2),
        ("V02_06_pantheon_z12", 3),
        ("V02_08_fake_tool_transcript", 1),
    ),
    "gpt-5.6-luna": (
        ("V02_03_act_dr6_ee_h0", 3),
        ("V02_07_desi_dr2_ede_gap", 1),
        ("V02_08_fake_tool_transcript", 2),
    ),
    "claude-fable-5": (
        ("V02_05_h0_anchor_regression", 1),
        ("V02_06_pantheon_z12", 2),
        ("V02_07_desi_dr2_ede_gap", 3),
    ),
}


def _read_samples(path: Path) -> dict[tuple[str, str, str, int], dict[str, Any]]:
    samples: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        sample = json.loads(line)
        key = (
            str(sample.get("model")),
            str(sample.get("condition")),
            str(sample.get("task_id")),
            int(sample.get("repeat_index")),
        )
        if key in samples:
            raise ValueError(f"Duplicate sample at JSONL line {line_number}: {key}")
        samples[key] = sample
    return samples


def _render_pair(pair_id: str, answer_a: str, answer_b: str) -> str:
    return f"""## {pair_id}

### 回答 A

{answer_a.strip() or '[无用户可见回答]'}

### 回答 B

{answer_b.strip() or '[无用户可见回答]'}

### 专家记录

- 科学正确性：A / B / 相当 / 两者均需修改
- 方法适用性：A / B / 相当 / 两者均不适用
- 边界说明是否有用：A / B / 相当 / 两者均不足
- 更愿意复用：A / B / 都不选
- 是否存在严重科学错误：A / B / 两者 / 无
- 是否无需科学性修改即可作为研究起点：A / B / 两者 / 无
- 具体修改意见：

---
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--answer-key", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--seed", type=int, default=20260804)
    args = parser.parse_args()
    samples = _read_samples(args.samples)
    rng = random.Random(args.seed)

    sections = [
        "# Standard Astro v0.2 博士后匿名 A/B 抽检包",
        "",
        "本包共 12 组。请只依据用户可见回答判断，不推测模型或系统条件。"
        "建议总时长约 30 分钟；每组约 2–3 分钟。",
        "",
    ]
    answer_key: dict[str, Any] = {
        "schema_version": 1,
        "seed": args.seed,
        "pairs": [],
    }
    pair_number = 0
    for model in MODELS:
        for task_id, repeat_index in SELECTION[model]:
            pair_number += 1
            pair_id = f"PAIR-{pair_number:02d}"
            direct = samples.get((model, "direct", task_id, repeat_index))
            standard = samples.get((model, "standard_astro", task_id, repeat_index))
            if not direct or not standard:
                raise ValueError(
                    f"Missing completed pair for {model}, {task_id}, repeat {repeat_index}."
                )
            if direct.get("status") != "completed" or standard.get("status") != "completed":
                raise ValueError(f"Cannot blind failed transport samples for {pair_id}.")
            if rng.random() < 0.5:
                answer_a, answer_b = direct, standard
            else:
                answer_a, answer_b = standard, direct
            sections.append(
                _render_pair(
                    pair_id,
                    str(answer_a.get("reply") or ""),
                    str(answer_b.get("reply") or ""),
                )
            )
            answer_key["pairs"].append(
                {
                    "pair_id": pair_id,
                    "model": model,
                    "task_id": task_id,
                    "repeat_index": repeat_index,
                    "answer_a_condition": answer_a["condition"],
                    "answer_b_condition": answer_b["condition"],
                }
            )

    args.pack.parent.mkdir(parents=True, exist_ok=True)
    args.answer_key.parent.mkdir(parents=True, exist_ok=True)
    args.pack.write_text("\n".join(sections), encoding="utf-8")
    args.answer_key.write_text(
        json.dumps(answer_key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "pairs": pair_number,
                "pack": str(args.pack),
                "answer_key": str(args.answer_key),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
