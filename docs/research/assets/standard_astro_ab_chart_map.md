# Standard Astro A/B figure map

Source: `standard_astro_ab_scores.csv` (24 completed responses; one row per model × condition × case).

| Figure | Analytical question | Family / type | Fields | Supported claim | Palette / non-color encoding |
|---|---|---|---|---|---|
| `standard_astro_ab_overall` | Does Standard Astro improve the audited total score across the completed matrix? | Comparison / zero-baseline vertical bar | `condition`, sum(`total`), sum(`max_score`) | Direct = 106/144; Standard Astro = 124/144; difference = +18 points / +12.5 percentage points | gold vs blue; direct labels and distinct edge colors |
| `standard_astro_ab_by_model` | Is the direction of the score difference consistent across the four models? | Comparison / grouped horizontal bar | `model`, `condition`, sum(`total`), sum(`max_score`) | Standard Astro = 31/36 for every model; direct = 29, 29, 26, 22 | gold vs blue; hatch on direct bars; direct value labels |
| `standard_astro_ab_by_task` | Does the condition difference vary across the three fixed task types? | Comparison / zero-baseline grouped vertical bar | `case_id`, `condition`, sum(`total`), sum(`max_score`) | A2 = 34 vs 48; B1 = 34 vs 44; C1 = 38 vs 32 | gold vs blue; hatch on direct bars; exact labels |
| `standard_astro_ab_task_profile` | How do the two conditions compare across the fixed A2/B1/C1 task order? | Comparison / categorical line-and-marker profile | `case_id`, fixed task order, `condition`, sum(`total`) | Standard Astro is higher on A2/B1 and lower on C1 | dashed circles vs solid squares; exact labels; bilingual non-time-trend warning |
| `standard_astro_ab_model_dimensions` | Which rubric components contribute to each model's condition-level total? | Matrix / two-panel annotated heatmap | `model`, `condition`, six rubric fields summed over three tasks | Numeric evidence improves across all four models; component trade-offs remain visible | gold-root vs blue-root sequential scales; shared 0–6 domain; exact cell labels |

The bar figures use absolute zero baselines and show exact numerator/denominator labels. The heatmap uses a shared 0–6 scale and exact cell labels. The task-profile line connects three discrete task categories only: it is explicitly labeled “任务剖面，非时间趋势” and supports no temporal or statistical trend inference. No confidence intervals are drawn because the design has one run per model × condition × case and does not estimate sampling variance.
