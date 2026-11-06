"""Summarize EASE's RE1 or TORAI-preprocessed RE2 service rankings."""

import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "RCAEval"))

from RCAEval.benchmark.evaluation import Evaluator
from RCAEval.benchmark.datasets import DATASET_MAP
from RCAEval.benchmark.scoring import service_ranks
from RCAEval.classes.graph import Node


def score(cases):
    evaluator = Evaluator()
    reciprocal_ranks = []
    for case in cases:
        ranks = service_ranks(case["ranks"])
        services = [node.entity for node in ranks]
        reciprocal_ranks.append(
            1.0 / (services.index(case["service"]) + 1)
            if case["service"] in services else 0.0
        )
        evaluator.add_case(
            ranks=ranks,
            answer=Node(case["service"], "unknown"),
        )
    return {
        "cases": evaluator.num,
        **{f"AC@{k}": evaluator.accuracy(k) for k in range(1, 6)},
        "Avg@5": evaluator.average(5),
        "MRR": sum(reciprocal_ranks) / len(reciprocal_ranks),
    }


def grouped(cases, keys):
    groups = {}
    for case in cases:
        key = tuple(case[name] for name in keys)
        groups.setdefault(key, []).append(case)
    return [
        {**dict(zip(keys, key)), **score(group)}
        for key, group in sorted(groups.items())
    ]


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def table(rows, keys):
    columns = [*keys, "cases", "AC@1", "AC@3", "AC@5", "Avg@5", "MRR"]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        cells = [
            (
                f"{100 * row[col]:.2f}%"
                if col.startswith(("AC@", "Avg@"))
                else f"{row[col]:.6f}" if col == "MRR" else str(row[col])
            )
            for col in columns
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("re1", "re2"), default="re2")
    args = parser.parse_args()
    datasets = (
        ("re1-ob", "re1-ss", "re1-tt")
        if args.suite == "re1" else ("torai-ob", "torai-ss", "torai-tt")
    )
    expected_count = 125 if args.suite == "re1" else 90
    cases = []
    for dataset in datasets:
        data_dir = ROOT / "RCAEval" / DATASET_MAP[dataset]
        result_dir = ROOT / "temp/output/ease" / dataset / "results"
        injections = sorted(data_dir.glob("*/*/inject_time.txt"))
        if len(injections) != expected_count:
            raise ValueError(
                f"{dataset}: expected {expected_count} input cases, found {len(injections)}"
            )
        expected = {f"{p.parent.parent.name}_{p.parent.name}.json" for p in injections}
        actual = {p.name for p in result_dir.glob("*.json")}
        if actual != expected:
            raise ValueError(
                f"{dataset}: missing results {sorted(expected - actual)}; unexpected results {sorted(actual - expected)}"
            )
        for path in injections:
            service, fault = path.parent.parent.name.rsplit("_", 1)
            case_id = path.parent.name
            prediction = json.loads(
                (result_dir / f"{service}_{fault}_{case_id}.json").read_text()
            )
            if set(prediction) != {"0"} or not isinstance(prediction["0"], list):
                raise ValueError(
                    f"Invalid prediction for {dataset}/{service}_{fault}/{case_id}"
                )
            ranks = prediction["0"]
            ranked_services = [node.entity for node in service_ranks(ranks)]
            rank = (
                ranked_services.index(service) + 1
                if service in ranked_services
                else None
            )
            cases.append(
                {
                    "dataset": dataset,
                    "service": service,
                    "fault": fault,
                    "case": case_id,
                    "root_cause_rank": rank,
                    "ranks": ranks,
                }
            )

    summary = [{"dataset": "overall", **score(cases)}, *grouped(cases, ["dataset"])]
    by_fault = [
        *[{"dataset": "overall", **row} for row in grouped(cases, ["fault"])],
        *grouped(cases, ["dataset", "fault"]),
    ]
    by_service_fault = grouped(cases, ["dataset", "service", "fault"])
    output = ROOT / "temp/results" / args.suite
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "summary.csv", summary)
    write_csv(output / "by_fault.csv", by_fault)
    write_csv(output / "by_service_fault.csv", by_service_fault)
    write_csv(
        output / "cases.csv",
        [{**case, "ranks": json.dumps(case["ranks"])} for case in cases],
    )
    suite_name = "RE1" if args.suite == "re1" else "TORAI-preprocessed RE2"
    cases_per_fault = 25 if args.suite == "re1" else 15
    window = "up to 600 metric rows on each side of injection"
    if args.suite == "re2":
        window += " and 40 rows per log/trace modality on each side"
    report = [
        f"# EASE: {suite_name} results",
        f"Service-level evaluation on all {len(cases)} cases. Each dataset has {expected_count} cases; each fault type has {cases_per_fault} cases per dataset.",
        f"The run uses the original RCAEval loader with --length 20 --tdelta 0: {window}. Original missing-value filling is preserved. All cases are included, including cases with incomplete telemetry.",
        "AC@k is the fraction of cases whose root-cause service appears in the first k distinct services. Avg@5 is the mean of AC@1 through AC@5. MRR is the mean reciprocal root-cause rank over the complete service ranking, with zero for an absent root cause. Overall scores are weighted by case count. EASE returns service rankings, so indicator-level accuracy is not reported.",
        "## Overall and per dataset",
        table(summary, ["dataset"]),
        "## Per fault type",
        table(by_fault, ["dataset", "fault"]),
        "## Per service and fault type",
        table(by_service_fault, ["dataset", "service", "fault"]),
        "The CSV files retain unrounded scores. [cases.csv](cases.csv) contains every prediction and the resulting root-cause rank.",
    ]
    (output / "README.md").write_text("\n\n".join(report) + "\n")
    print(table(summary, ["dataset"]))


if __name__ == "__main__":
    main()
