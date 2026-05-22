"""Sandbox language guard tests — all run_python external output must be standard English.

Coverage:
- English stdout + English figure -> pass
- Chinese print output -> warning, successful computation must not be downgraded to Partial
- Chinese figure title -> triggers TextLanguageError
- Greek letters (LaTeX) / Angstrom / degree / +/- / >= must not be flagged
- Japanese / Korean / full-width punctuation are also blocked
- _classify_sandbox_error("TextLanguageError: ...") -> "non_english_output"
"""
from __future__ import annotations

import pytest

from app.services.sandbox.subprocess_backend import (
    _detect_non_english,
    SubprocessBackend,
)
from app.services.ai_tools import _classify_sandbox_error


# ---- Unit tests: _detect_non_english ----


def test_detect_english_returns_empty():
    assert _detect_non_english("Pleiades CMD: N = 776 stars") == []


def test_detect_greek_latex_passes():
    # LaTeX macros are raw ASCII and contain no CJK characters
    assert _detect_non_english(r"$\alpha$ Cen A, $T_{\rm eff}$ = 5800 K") == []


def test_detect_scientific_unicode_passes():
    # Angstrom (U+00C5), degree (U+00B0), +/- (U+00B1), >= (U+2265), approximately (U+2248)
    assert _detect_non_english("6563 Å, ±0.3 mag, ≈5780 K, RA 180°") == []


def test_detect_accented_latin_passes():
    # Extended Latin (accented letters) must not be blocked — font-supported and not CJK blocks
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
    # Full-width colon U+FF1A, full-width comma U+FF0C (different from ASCII `:` / `,`)
    hits = _detect_non_english("RA：180，Dec：0")
    assert len(hits) >= 1


def test_detect_max_samples_caps_output():
    text = "一 二 三 四 五 六 七 八"
    hits = _detect_non_english(text, max_samples=3)
    assert len(hits) == 3


def test_detect_empty_string():
    assert _detect_non_english("") == []


# ---- Unit tests: _classify_sandbox_error ----


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
    # Even if the error contains "SyntaxError", non_english_output takes precedence
    err = "TextLanguageError: 出现了 print() with SyntaxError fallback"
    assert _classify_sandbox_error(err) == "non_english_output"


# ---- Integration tests: actually running the subprocess sandbox ----


@pytest.mark.slow
def test_subprocess_english_print_passes():
    """English print output is not blocked."""
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
    """R0: Chinese print output is a logging/UI issue and must not downgrade a successful computation to Partial."""
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
    """A Chinese matplotlib title is hard-blocked."""
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
    """LaTeX Greek letter titles must not be falsely flagged."""
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
    """Scientific Unicode scalars such as Angstrom, degree, and +/- must not be falsely flagged."""
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
    """Chinese characters in xlabel are also detected (not just in the title)."""
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
