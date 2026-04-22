"""Sandbox 语言守护测试 — 所有 run_python 对外输出必须是标准英语。

覆盖:
- 英语 stdout + 英语 figure → 通过
- 中文 print 输出 → warning, 不让成功计算变 Partial
- 中文 figure title → 触发 TextLanguageError
- 希腊字母 (LaTeX) / Å / ° / ± / ≥ 不误判
- 日语 / 韩语 / 全角标点也被拦
- _classify_sandbox_error("TextLanguageError: ...") → "non_english_output"
"""
from __future__ import annotations

import pytest

from app.services.sandbox.subprocess_backend import (
    _detect_non_english,
    SubprocessBackend,
)
from app.services.ai_tools import _classify_sandbox_error


# ---- 单元测试: _detect_non_english ----


def test_detect_english_returns_empty():
    assert _detect_non_english("Pleiades CMD: N = 776 stars") == []


def test_detect_greek_latex_passes():
    # LaTeX macros 是 raw ASCII, 不含 CJK
    assert _detect_non_english(r"$\alpha$ Cen A, $T_{\rm eff}$ = 5800 K") == []


def test_detect_scientific_unicode_passes():
    # Å (U+00C5), ° (U+00B0), ± (U+00B1), ≥ (U+2265), ≈ (U+2248)
    assert _detect_non_english("6563 Å, ±0.3 mag, ≈5780 K, RA 180°") == []


def test_detect_accented_latin_passes():
    # 拉丁扩展 (accented letters) 不拦 — 字体支持, 也不是方块
    assert _detect_non_english("naïve façade résumé") == []


def test_detect_chinese_finds_substring():
    hits = _detect_non_english("成员星数量: 776")
    assert hits == ["成员星数量"]


def test_detect_japanese_finds_substring():
    hits = _detect_non_english("時間 (BJD)")
    assert "時間" in hits[0]


def test_detect_hangul_finds_substring():
    hits = _detect_non_english("플레이아데스")
    assert len(hits) == 1
    assert "플레이아데스" in hits[0]


def test_detect_fullwidth_punctuation_finds_substring():
    # 全角冒号 U+FF1A, 全角逗号 U+FF0C (与 ASCII `:` / `,` 不同)
    hits = _detect_non_english("RA：180，Dec：0")
    assert len(hits) >= 1


def test_detect_max_samples_caps_output():
    text = "一 二 三 四 五 六 七 八"
    hits = _detect_non_english(text, max_samples=3)
    assert len(hits) == 3


def test_detect_empty_string():
    assert _detect_non_english("") == []


# ---- 单元测试: _classify_sandbox_error ----


def test_classify_textlanguageerror_to_non_english():
    err = (
        "TextLanguageError: run_python produced non-English text in stdout "
        "or figure text (renders as tofu squares in the sandbox font)..."
    )
    assert _classify_sandbox_error(err) == "non_english_output"


def test_classify_lowercase_non_english_phrase():
    err = "non-English characters detected in figure title"
    assert _classify_sandbox_error(err) == "non_english_output"


def test_classify_textlanguageerror_takes_precedence_over_syntaxerror():
    # 哪怕 error 里也含 SyntaxError 字样, non_english_output 优先
    err = "TextLanguageError: 出现了 print() with SyntaxError fallback"
    assert _classify_sandbox_error(err) == "non_english_output"


# ---- 集成测试: 真的跑一次 subprocess sandbox ----


@pytest.mark.slow
def test_subprocess_english_print_passes():
    """英语 print 不被拦。"""
    backend = SubprocessBackend()
    result = backend.execute(
        'print("Pleiades N = 776 stars, parallax 7.353 mas")',
        timeout=30,
        memory_bytes=512 * 1024 * 1024,
    )
    assert result.success is True
    assert "776" in result.stdout
    assert result.error is None


@pytest.mark.slow
def test_subprocess_chinese_print_warns_without_failing():
    """R0: 中文 print 是日志/UI问题, 不应把科学计算降成 Partial。"""
    backend = SubprocessBackend()
    result = backend.execute(
        'print("成员星数量: 776")',
        timeout=30,
        memory_bytes=512 * 1024 * 1024,
    )
    assert result.success is True
    assert result.error is None
    assert "language warning" in result.stderr
    assert "print()" in result.stderr
    assert "成员星数量" in result.stdout


@pytest.mark.slow
def test_subprocess_chinese_figure_title_raises():
    """中文 matplotlib title 被硬拦。"""
    backend = SubprocessBackend()
    code = (
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([1, 2, 3], [1, 4, 9])\n"
        "ax.set_title('昴星团 CMD')\n"
    )
    result = backend.execute(
        code, timeout=30, memory_bytes=512 * 1024 * 1024
    )
    assert result.success is False
    assert result.error is not None
    assert "TextLanguageError" in result.error
    assert "figure" in result.error.lower()


@pytest.mark.slow
def test_subprocess_greek_latex_title_passes():
    """LaTeX Greek letter title 不误判。"""
    backend = SubprocessBackend()
    code = (
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([1, 2, 3], [1, 4, 9])\n"
        "ax.set_title(r'$\\alpha$ Cen HR diagram')\n"
        "ax.set_xlabel('BP - RP (mag)')\n"
        "ax.set_ylabel(r'Absolute $M_G$ (mag)')\n"
    )
    result = backend.execute(
        code, timeout=30, memory_bytes=512 * 1024 * 1024
    )
    assert result.success is True, f"expected success, got error: {result.error}"


@pytest.mark.slow
def test_subprocess_angstrom_degree_pass():
    """Å, °, ± 等标量科学 Unicode 不误判。"""
    backend = SubprocessBackend()
    code = (
        'print("6563 Å H-alpha, RA=180°, T_eff=5800±50 K")\n'
    )
    result = backend.execute(
        code, timeout=30, memory_bytes=512 * 1024 * 1024
    )
    assert result.success is True, f"expected success, got error: {result.error}"
    assert "Å" in result.stdout


@pytest.mark.slow
def test_subprocess_chinese_xlabel_caught():
    """xlabel 里的中文也被扫到 (不只是 title)。"""
    backend = SubprocessBackend()
    code = (
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([1, 2], [1, 4])\n"
        "ax.set_xlabel('颜色')\n"  # Chinese xlabel
        "ax.set_ylabel('Magnitude')\n"
    )
    result = backend.execute(
        code, timeout=30, memory_bytes=512 * 1024 * 1024
    )
    assert result.success is False
    assert "TextLanguageError" in (result.error or "")
