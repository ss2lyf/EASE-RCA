#!/usr/bin/env bash
# Run EASE on a complete benchmark suite, then summarize its service rankings.
set -euo pipefail

usage() {
    echo "Usage: bash scripts/run.sh [re2|re1|all]"
    echo "Default: re2. Uses the active Python environment (or PYTHON=/path/to/python)."
}

if [[ $# -gt 1 ]]; then
    usage >&2
    exit 2
fi
case "${1:-re2}" in
    re1) suites=(re1) ;;
    re2) suites=(re2) ;;
    all) suites=(re2 re1) ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python}"
export PYTHONDONTWRITEBYTECODE=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
cd "$repo_root/RCAEval"

# Check all selected inputs before starting a run or triggering native downloads.
"$python_bin" - "${suites[@]}" <<'PY'
from pathlib import Path
import sys

from RCAEval.benchmark.datasets import DATASET_MAP

for suite in sys.argv[1:]:
    prefix, expected = ("re1", 125) if suite == "re1" else ("torai", 90)
    for system in ("ob", "ss", "tt"):
        dataset = f"{prefix}-{system}"
        directory = Path(DATASET_MAP[dataset])
        cases = sorted(directory.glob("*/*/inject_time.txt"))
        if len(cases) != expected:
            raise SystemExit(
                f"{dataset}: expected {expected} cases in {directory}, found {len(cases)}. "
                "Follow the Data section of the README."
            )
        required = ["data.csv"] if suite == "re1" else ["simple_metrics.csv", "logts.csv"]
        if suite == "re2" and system != "ss":
            required += ["tracets_err.csv", "tracets_lat.csv"]
        for case in cases:
            for name in required:
                path = case.parent / name
                if not path.is_file():
                    raise SystemExit(f"Missing input: {path}")
PY

for suite in "${suites[@]}"; do
    if [[ "$suite" == re1 ]]; then
        datasets=(re1-ob re1-ss re1-tt)
    else
        datasets=(torai-ob torai-ss torai-tt)
    fi
    for dataset in "${datasets[@]}"; do
        echo "Running EASE: $dataset"
        "$python_bin" main.py --method ease --dataset "$dataset" --length 20 --tdelta 0
    done
    "$python_bin" "$repo_root/summarize_results.py" --suite "$suite"
done
