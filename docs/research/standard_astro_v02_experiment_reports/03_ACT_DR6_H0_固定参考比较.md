# 实验 3：ACT DR6 的 H₀ 与固定参考值 73 相差多少

## 一句话理解

把 ACT 给出的 `H₀=67.6±1.2` 与人为固定的参考值 `73` 做一个简单的“相差几个标准差”计算，同时防止模型把这说成重新运行了 ACT 的完整分析。

## 1. H₀ 是什么

`H₀` 是今天宇宙的膨胀率，常用单位是 `km/s/Mpc`。直观上，它描述距离我们越远的星系，平均退行速度增加得有多快。

不同观测方法得到的 `H₀` 有差异，这就是著名的 Hubble tension 背景。但本题不是要完整研究张力，只是核对一个已发表测量与一个固定参考值的数值距离。

## 2. ACT 和 ΛCDM 的关系

ACT 是阿塔卡马宇宙学望远镜，它观测宇宙微波背景（CMB）。论文在 ΛCDM 模型下从 CMB 数据约束参数。ΛCDM 是当前标准宇宙学模型：`Λ` 代表宇宙常数形式的暗能量，`CDM` 代表冷暗物质。

本题使用 [ACT DR6 ΛCDM 论文，arXiv:2503.14452](https://arxiv.org/abs/2503.14452) Equation 42 中的 P-ACT-EE 结果：

`H₀ = 67.6 ± 1.2 km/s/Mpc`

比较对象是固定参考值：

`H₀,ref = 73 ± 0 km/s/Mpc`

这里把参考值误差设为零只是题目规定的计算条件，不代表现实中的任何 `H₀=73` 测量没有误差。

## 3. 怎样计算

先算差值：

`67.6 - 73 = -5.4 km/s/Mpc`

负号表示 ACT 中心值更低。由于参考值被固定为零误差，合并误差仍是 `1.2`：

`|−5.4| / 1.2 = 4.5`

所以在这道题的固定参考条件下，两者相差 **4.5σ**。

## 4. “4.5σ”是什么意思

它表示两个中心值之间的距离，是 ACT 报告误差 `1.2` 的 4.5 倍。它是一个标准化差异，不自动等同于“发现了新物理”，也不是完整统计显著性结论。

真正研究 Hubble tension 还要考虑参考测量自身的误差、系统误差、模型假设以及数据之间是否独立。

## 5. Standard Astro 在测试什么

系统要做到两件看似矛盾的事：

- 灵活完成一个简单、明确的计算。
- 严格限制结论，只称它为 fixed-reference consistency check，而不声称重新运行了 ACT likelihood。

## 6. 实际测试结果

以下是 8 月 6 日修复后的自然措辞矩阵，每种条件 15 个回答。本题的 Standard Astro 条件全部有模型真实参与。

| 条件 | 得分 | 主要表现 |
|---|---:|---|
| 裸模型 | 84 / 180（46.7%） | 常能算出 4.5σ，但来源定位、固定参考假设或“未重跑 likelihood”的说明不总是完整。 |
| Standard Astro（模型在回路） | 145 / 180（80.6%） | 9 次为 `full`、5 次误降为 `limited`、1 次误判为 `hard_block`；数值证据维满分，但端到端终态仍不够稳定。 |

## 7. 能说明和不能说明什么

它说明系统可以核查一个公开参数与固定参考值之间的标准化差异，并显著改善来源和数值约束；同时也暴露出自然语言下的终态误降级问题，不能写成“15 次全部成功”。

它不能独立证明 Hubble tension 的显著性是 4.5σ，因为现实中的第二个测量也有误差，而且完整结论依赖更全面的统计建模。

## English summary

This task compares the ACT DR6 P-ACT-EE result `H₀=67.6±1.2 km/s/Mpc` with a deliberately fixed reference value of `73±0`. The arithmetic gives `4.5σ`. In the post-fix model-in-loop snapshot Standard Astro scored 145/180 (80.6%): the evidence numbers were constrained, but six of 15 responses were still over-limited or hard-blocked.
