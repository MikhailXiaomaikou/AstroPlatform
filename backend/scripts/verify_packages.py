"""Verify all professional astronomy packages can be imported.

Run this after deployment to confirm all dependencies installed correctly.

Usage:
    python scripts/verify_packages.py
"""

PACKAGES = [
    # Core astronomy
    ("astropy", "astropy"),
    ("astroquery", "astroquery"),
    # Professional analysis (added in professional upgrade)
    ("specutils", "specutils"),
    ("photutils", "photutils"),
    ("reproject", "reproject"),
    ("dynesty", "dynesty"),
    ("ultranest", "ultranest"),
    ("arviz", "arviz"),
    ("batman", "batman"),
    ("celerite2", "celerite2"),
    ("pyvo", "pyvo"),
    ("lightkurve", "lightkurve"),
    # Data processing
    ("numpy", "numpy"),
    ("scipy", "scipy"),
    ("pandas", "pandas"),
    ("dask", "dask"),
    ("dask.dataframe", "dask.dataframe"),
    # ML & statistics
    ("scikit-learn", "sklearn"),
    ("emcee", "emcee"),
    ("corner", "corner"),
    # Visualization
    ("matplotlib", "matplotlib"),
    ("plotly", "plotly"),
    # Web framework
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
]


def main():
    passed = 0
    failed = 0

    print("Verifying package imports:\n")

    for display_name, import_name in PACKAGES:
        try:
            mod = __import__(import_name)
            version = getattr(mod, "__version__", "?")
            print(f"  \u2705 {display_name:20s} {version}")
            passed += 1
        except ImportError as e:
            print(f"  \u274c {display_name:20s} {e}")
            failed += 1

    print(f"\n  Result: {passed}/{passed + failed} packages available")

    if failed:
        print(f"\n  {failed} package(s) missing. Install with:")
        print("  pip install --require-hashes -r requirements.lock")

    return failed == 0


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)
