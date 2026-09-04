#!/usr/bin/env python3
"""Produce the preregistered platform-exact Planck H0 chain (route B, stage 0/1).

Two subcommands, both bound to backend/scripts/cobaya/platform_h0_prereg.json:

  generate  — render the Cobaya input YAML through the production code path
              (the same parameter order, prior sanitization, and YAML builder
              the chat tool would use) and print the exact mpirun command.
  manifest  — after the offline run, hash every produced artifact and record
              the environment inventory into production_manifest.json.

The formal run itself is plain `cobaya run` under mpirun (resumable with -r);
this script never samples. Run from backend/:

    ./venv/bin/python scripts/cobaya/produce_platform_chain.py generate
    caffeinate -dims mpirun -n 4 ./venv/bin/python -m cobaya run \
        .local/platform_chains/platform_planck2018_native_lcdm_h0_v1/platform_h0.input.yaml -r
    ./venv/bin/python scripts/cobaya/produce_platform_chain.py manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

PREREG_PATH = Path(__file__).resolve().parent / "platform_h0_prereg.json"
DEFAULT_OUTPUT_DIR = BACKEND_ROOT / ".local" / "platform_chains"


def _load_prereg() -> dict:
    return json.loads(PREREG_PATH.read_text())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _run_dir(prereg: dict, output_dir: str | None) -> Path:
    base = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    return base / prereg["profile_id"]


def cmd_generate(args: argparse.Namespace) -> int:
    prereg = _load_prereg()
    commitments = prereg["sampler_commitments"]

    # Production code paths only: any drift between this YAML and what the
    # chat tool would run breaks the "platform-exact" semantics.
    from app.services.cobaya_runner import _build_cobaya_yaml
    from app.services.cosmology_likelihoods.runners import (
        _cobaya_parameter_order,
        _sanitize_runner_priors,
        _validate_dataset_selection,
        _validate_model,
    )

    model_key = _validate_model(prereg["model"])
    entries = _validate_dataset_selection(model_key, list(prereg["dataset_keys"]))
    parameter_order = _cobaya_parameter_order(model_key, entries)
    prior_bounds = _sanitize_runner_priors(parameter_order, None)

    run_dir = _run_dir(prereg, args.output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = run_dir / "chain"

    yaml_text = _build_cobaya_yaml(
        model_key=model_key,
        entries=entries,
        prior_bounds=prior_bounds,
        parameter_order=parameter_order,
        sampler=commitments["sampler"],
        output_prefix=output_prefix,
        seed=int(commitments["seed"]),
    )
    yaml_path = run_dir / "platform_h0.input.yaml"
    yaml_path.write_text(yaml_text)

    thread_env = " ".join(
        f"{key}={value}" for key, value in commitments["thread_env"].items()
    )
    print(f"profile:     {prereg['profile_id']}")
    print(f"input yaml:  {yaml_path}")
    print(f"yaml sha256: {_sha256_file(yaml_path)}")
    print(f"generator commit: {_git_head()}")
    print()
    print("Formal run (resumable with -r after interruption):")
    print(
        f"  caffeinate -dims env {thread_env} "
        f"mpirun -n {commitments['mpi_chains']} "
        f"{BACKEND_ROOT}/venv/bin/python -m cobaya run {yaml_path} -r"
    )
    return 0


def _package_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    versions: dict[str, str] = {}
    for name in ("cobaya", "camb", "getdist", "numpy", "scipy", "arviz"):
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def cmd_manifest(args: argparse.Namespace) -> int:
    prereg = _load_prereg()
    run_dir = _run_dir(prereg, args.output_dir)
    if not run_dir.is_dir():
        print(f"run directory not found: {run_dir}", file=sys.stderr)
        return 1

    artifacts = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name == "production_manifest.json":
            continue
        artifacts.append(
            {
                "relative_path": str(path.relative_to(run_dir)),
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    if not artifacts:
        print(f"no artifacts found under {run_dir}", file=sys.stderr)
        return 1

    # sys.executable -m pip works in both the local venv and CI's system
    # interpreter (CI has no backend/venv), and records the interpreter that
    # actually runs the chain.
    freeze = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    manifest = {
        "schema_version": 1,
        "profile_id": prereg["profile_id"],
        "prereg_sha256": _sha256_file(PREREG_PATH),
        "generator_commit": _git_head(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": sys.version,
            "packages": _package_versions(),
            "pip_freeze_sha256": hashlib.sha256(freeze.encode()).hexdigest(),
            "macos": platform.platform(),
            "thread_env_commitment": prereg["sampler_commitments"]["thread_env"],
        },
        "artifacts": artifacts,
    }
    manifest_path = run_dir / "production_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest:    {manifest_path}")
    print(f"artifacts:   {len(artifacts)}")
    print(f"manifest sha256: {_sha256_file(manifest_path)}")
    print()
    print(
        "Next: check convergence and the preregistered acceptance criteria "
        "(R-hat < 1.01, bulk ESS >= 400, H0 consistency), then register the "
        "pinned hashes in platform_chain_registry (stage 2)."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Render the preregistered input YAML")
    gen.add_argument("--output-dir", default=None, help="Base output directory (default: backend/.local/platform_chains)")
    gen.set_defaults(func=cmd_generate)

    man = sub.add_parser("manifest", help="Hash run artifacts into production_manifest.json")
    man.add_argument("--output-dir", default=None, help="Base output directory used at generate time")
    man.set_defaults(func=cmd_manifest)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
