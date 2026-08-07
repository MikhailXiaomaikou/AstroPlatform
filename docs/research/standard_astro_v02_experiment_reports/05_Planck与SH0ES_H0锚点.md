# 实验 5：Planck 与 SH0ES 的 H₀ 锚点比较

## 一句话理解

比较两组已经登记并带来源的 H₀ 数值，确认系统在新会话、缓存为空时仍能回答，而不会误走需要观测缓存的距离计算。

## 1. 为什么这两个 H₀ 值重要

Planck 主要利用早期宇宙的 CMB，在 ΛCDM 模型下间接推断今天的 H₀。SH0ES 主要利用本地距离梯——例如造父变星和 Ia 型超新星——测量晚期宇宙的 H₀。

两条路线给出的中心值不同，是 Hubble tension 的核心背景之一。但本实验只比较两个已注册锚点，不重做两套完整分析。

## 2. 注册值

| 来源 | H₀（km/s/Mpc） |
|---|---:|
| Planck 2018 CMB-only | `67.36 ± 0.54` |
| Riess et al. 2022 SH0ES | `73.04 ± 1.04` |

来源标识分别绑定到 Planck 2018 的正式结果和 Riess et al. 2022 SH0ES 结果，而不是模型凭记忆写出的数字。

## 3. 百分比差异怎样算

中心值差：

`73.04 - 67.36 = 5.68 km/s/Mpc`

以 Planck 值为分母：

`5.68 / 67.36 × 100% = 8.43%`

所以 SH0ES 的中心值比 Planck 锚点高约 **8.43%**。

## 4. 标准化差异怎样算

若按独立近似合并误差：

`sqrt(0.54² + 1.04²) ≈ 1.172`

于是：

`5.68 / 1.172 ≈ 4.85σ`

这给出约 **4.85σ** 的锚点差异。

## 5. 这项实验还在检查一个工程问题

昨天系统曾把正常的 Hubble tension 问题路由到 `compare_luminosity_distances`，而这个工具依赖新会话里尚未准备好的 measurement cache。于是系统还没走到科学护栏，就因为“缓存为空”回答不了。

修复后，这类锚点比较直接读取带引用的注册值，不再把缓存当作必要前提。换句话说：

- 注册锚点是这道题真正需要的数据。
- 距离测量缓存不应该成为无关的阻塞条件。
- 没有重新运行距离拟合，就不能声称做了新的 H₀ 测量。

## 6. 实际测试结果

以下是 8 月 6 日修复后的自然措辞矩阵，每种条件 15 个回答。本题的 Standard Astro 条件全部有模型真实参与。

| 条件 | 得分 | 主要表现 |
|---|---:|---|
| 裸模型 | 47 / 180（26.1%） | 容易凭记忆混用不同年份或不同数据组合的 H₀ 数值，来源和比较口径不稳定。 |
| Standard Astro（模型在回路） | 180 / 180（100.0%） | 15 次都使用同一组注册锚点，得到 8.43% 与 4.85σ，并说明这不是新拟合。 |

## 7. 能说明和不能说明什么

它说明系统已修复“空缓存导致正常问题过早失败”的具体缺陷，并能稳定地比较来源绑定的锚点。

它不能单独解决 Hubble tension，也不能代替对 Planck、SH0ES 系统误差和模型依赖的完整研究。

## English summary

This regression task compares citation-pinned Planck 2018 and SH0ES 2022 H₀ anchors: `67.36±0.54` versus `73.04±1.04 km/s/Mpc`. The center-value offset is 8.43%, and the independent-error approximation gives 4.85σ. In the post-fix natural-phrasing snapshot, all 15 model-in-loop responses passed the six automated dimensions and a fresh session no longer failed because an unrelated measurement cache was empty.
