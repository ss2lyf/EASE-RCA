#!/usr/bin/env python3
"""Convert RE3 logs and traces using the preprocessing from the EASE-RCA paper.

Write logts.csv and, when usable traces are available, tracets_err.csv and
tracets_lat.csv. By default, these derived files are placed beside the raw
inputs. With --output-root, mirror the case directories and copy their metrics
and injection timestamps instead. Original input files are left unchanged.

Logs and traces use 15-second buckets. Metric sampling is not changed here.
This is the paper's TORAI-compatible conversion, not TORAI's original pipeline.
"""

import argparse
import re
import shutil
from pathlib import Path
from typing import Iterable, Tuple

import numpy as np
import pandas as pd


def safe_name(value) -> str:
    """Make a stable token for a service or operation name in a CSV column."""
    text = str(value) if value is not None else "unknown"
    text = text.strip()
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "unknown"


def normalize_service_name(value) -> str:
    # Preserve the historical Trace naming; EASE aligns the frontend alias later.
    name = safe_name(value).lower()
    if name == "frontend":
        return "frontendservice"
    return name


def event_seconds(series: pd.Series, unit: str) -> pd.Series:
    """Convert event timestamps to integer Unix seconds, preserving missing values."""
    values = pd.to_numeric(series, errors="coerce")
    if unit == "ns":
        return np.floor(values / 1_000_000_000).astype("Int64")
    if unit == "ms":
        return np.floor(values / 1_000).astype("Int64")
    return np.floor(values).astype("Int64")


def bucket_seconds(seconds: pd.Series, interval_seconds: int) -> pd.Series:
    # Buckets are aligned to the Unix epoch, independently of fault injection.
    return (seconds // interval_seconds * interval_seconds).astype("Int64")


def complete_time_index(df: pd.DataFrame, interval_seconds: int) -> pd.DataFrame:
    """Fill absent buckets with zero between the first and last observed buckets."""
    if df.empty or "time" not in df:
        return df
    start = int(df["time"].min())
    end = int(df["time"].max())
    full = pd.DataFrame({"time": range(start, end + interval_seconds, interval_seconds)})
    return full.merge(df, on="time", how="left").fillna(0)


def normalize_log_message(message) -> str:
    """Replace variable message fields with tokens using the paper's regex rules."""
    text = str(message) if message is not None else ""
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[.,]\d+\b", "<time>", text)
    text = re.sub(r"\b\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}\s+[+-]\d{4}\b", "<time>", text)
    text = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        "<uuid>",
        text,
    )
    text = re.sub(r"\b[0-9a-fA-F]{8,}\b", "<hex>", text)
    text = re.sub(r"\b\d+\b", "<num>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:240]


def logs_to_logts(logs: pd.DataFrame, interval_seconds: int, top_templates: int) -> pd.DataFrame:
    """Count retained service/template pairs in each time bucket of one case."""
    required = {"timestamp", "container_name", "message"}
    if logs.empty or not required.issubset(logs.columns):
        return pd.DataFrame()

    work = logs.loc[:, ["timestamp", "container_name", "message"]].copy()
    work["time"] = bucket_seconds(event_seconds(work["timestamp"], "ns"), interval_seconds)
    work = work.dropna(subset=["time"])
    work["service"] = work["container_name"].map(safe_name)
    work["template"] = work["message"].map(normalize_log_message)

    # Rank templates across all services within this case. Resolve frequency
    # ties by service and template text so column selection is deterministic.
    counts = (
        work.groupby(["service", "template"], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values(["count", "service", "template"], ascending=[False, True, True])
    )
    counts["rank"] = range(1, len(counts) + 1)
    top = counts.head(top_templates).copy()
    template_to_rank = {
        (row.service, row.template): int(row.rank)
        for row in top.itertuples(index=False)
    }
    column_order = (top["service"] + "_" + top["rank"].astype(str)).tolist()
    work["rank"] = [template_to_rank.get((svc, tmpl)) for svc, tmpl in zip(work["service"], work["template"])]
    work = work.dropna(subset=["rank"])
    work["rank"] = work["rank"].astype(int)
    work["column"] = work["service"] + "_" + work["rank"].astype(str)

    table = (
        work.groupby(["time", "column"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    table = table.loc[:, ["time"] + [col for col in column_order if col in table.columns]]
    return complete_time_index(table, interval_seconds)


def parse_trace_endpoint(service_name, operation_name, method_name=None) -> Tuple[str, str]:
    """Recover the target service and operation from the recorded span fields."""
    operation = "" if pd.isna(operation_name) else str(operation_name).strip()
    method = "" if method_name is None or pd.isna(method_name) else str(method_name).strip()

    if "/" in operation:
        left, right = operation.rsplit("/", 1)
        service_token = left.strip("/").split(".")[-1]
        service = re.sub(r"Service$", "service", service_token)
        return normalize_service_name(service), safe_name(right)

    if operation:
        return normalize_service_name(service_name), safe_name(operation)

    return normalize_service_name(service_name), safe_name(method or "unknown")


def trace_column_series(traces: pd.DataFrame, top_operations: int) -> Tuple[pd.Series, list]:
    """Select the most frequent service/operation pairs within one case."""
    work = traces.loc[:, ["serviceName", "operationName", "methodName"]].copy()
    endpoints = [
        parse_trace_endpoint(row.serviceName, row.operationName, row.methodName)
        for row in work.itertuples(index=False)
    ]
    work["service"] = [service for service, _ in endpoints]
    work["operation"] = [operation for _, operation in endpoints]
    counts = (
        work.groupby(["service", "operation"], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values(["count", "service", "operation"], ascending=[False, True, True])
    )
    top = counts.head(top_operations)
    allowed = {(row.service, row.operation) for row in top.itertuples(index=False)}
    column_order = (top["service"] + "_" + top["operation"]).tolist()
    work = work[[pair in allowed for pair in zip(work["service"], work["operation"])]]
    return work["service"] + "_" + work["operation"], column_order


def traces_to_timeseries(
    traces: pd.DataFrame,
    interval_seconds: int,
    top_operations: int,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Aggregate span error counts and mean latency for each retained operation."""
    required = {"startTimeMillis", "serviceName", "methodName", "operationName", "duration", "statusCode"}
    if traces.empty or not required.issubset(traces.columns):
        return pd.DataFrame(), pd.DataFrame()

    work = traces.loc[:, ["startTimeMillis", "serviceName", "methodName", "operationName", "duration", "statusCode"]].copy()
    work["time"] = bucket_seconds(event_seconds(work["startTimeMillis"], "ms"), interval_seconds)
    work = work.dropna(subset=["time"])
    work["column"], column_order = trace_column_series(work, top_operations)
    work = work.dropna(subset=["column"])
    # RE3 span durations are microseconds; latency outputs are milliseconds.
    work["duration"] = pd.to_numeric(work["duration"], errors="coerce").fillna(0) / 1000.0
    status = pd.to_numeric(work["statusCode"], errors="coerce")
    # Preserve the paper's numeric nonzero-status rule; this is not an HTTP
    # status >= 400 test. Missing or nonnumeric statuses contribute no error.
    work["error"] = ((status.notna()) & (status != 0)).astype(int)

    err = (
        work.groupby(["time", "column"])["error"]
        .sum()
        .unstack(fill_value=0)
        .reset_index()
    )
    lat = (
        work.groupby(["time", "column"])["duration"]
        .mean()
        .unstack(fill_value=0)
        .reset_index()
    )
    ordered_columns = ["time"] + [col for col in column_order if col in err.columns]
    err = err.loc[:, ordered_columns]
    lat = lat.loc[:, ordered_columns]
    return complete_time_index(err, interval_seconds), complete_time_index(lat, interval_seconds)


def default_top_log_templates(case_dir: Path) -> int:
    """Use the paper's per-system limits without inspecting failure labels."""
    parts = set(case_dir.parts)
    if "RE3-TT" in parts:
        return 200
    return 50


def iter_case_dirs(input_root: Path) -> Iterable[Path]:
    """Find cases from raw telemetry, allowing a dataset root or a single case."""
    if (input_root / "logs.csv").exists() or (input_root / "traces.csv").exists():
        yield input_root
        return
    seen = set()
    for path in sorted(input_root.rglob("logs.csv")) + sorted(input_root.rglob("traces.csv")):
        case_dir = path.parent
        if case_dir not in seen:
            seen.add(case_dir)
            yield case_dir


def mirror_case_files(case_dir: Path, output_dir: Path) -> None:
    """Copy metric inputs and labels byte-for-byte when using a separate output."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("simple_metrics.csv", "data.csv", "metrics.csv", "inject_time.txt"):
        src = case_dir / name
        dst = output_dir / name
        if src.exists() and src.resolve() != dst.resolve():
            shutil.copy2(src, dst)


def process_case(case_dir: Path, output_dir: Path, args) -> None:
    """Write derived telemetry for one case without changing its raw inputs."""
    if output_dir != case_dir:
        mirror_case_files(case_dir, output_dir)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    logs_path = case_dir / "logs.csv"
    if logs_path.exists():
        top_log_templates = args.top_log_templates or default_top_log_templates(case_dir)
        logts = logs_to_logts(
            pd.read_csv(logs_path),
            interval_seconds=args.interval_seconds,
            top_templates=top_log_templates,
        )
        logts.to_csv(output_dir / "logts.csv", index=False)

    traces_path = case_dir / "traces.csv"
    if traces_path.exists():
        traces_err, traces_lat = traces_to_timeseries(
            pd.read_csv(traces_path),
            interval_seconds=args.interval_seconds,
            top_operations=args.top_trace_operations,
        )
        trace_outputs = (output_dir / "tracets_err.csv", output_dir / "tracets_lat.csv")
        if traces_err.empty or traces_lat.empty:
            for path in trace_outputs:
                if path.exists():
                    path.unlink()
        else:
            traces_err.to_csv(trace_outputs[0], index=False)
            traces_lat.to_csv(trace_outputs[1], index=False)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path, help="RE3 dataset root or one case directory.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Output root for mirrored TORAI-style files. If omitted, write logts/tracets files in-place.",
    )
    parser.add_argument("--interval-seconds", type=int, default=15, help="Aggregation interval. TORAI-style default is 15.")
    parser.add_argument("--top-log-templates", type=int, help="Keep top N service/template pairs per case. Default: 50 for RE3-OB/SS, 200 for RE3-TT.")
    parser.add_argument("--top-trace-operations", type=int, default=80, help="Keep top N target-service/operation pairs per case.")
    return parser.parse_args()


def main():
    args = parse_args()
    input_root = args.input_root.resolve()
    output_root = args.output_root.resolve() if args.output_root is not None else input_root

    case_dirs = list(iter_case_dirs(input_root))
    if not case_dirs:
        raise SystemExit(f"No logs.csv or traces.csv found under {input_root}")

    for idx, case_dir in enumerate(case_dirs, start=1):
        rel = case_dir.relative_to(input_root) if case_dir != input_root else Path(".")
        output_dir = case_dir if args.output_root is None else output_root / rel
        process_case(case_dir, output_dir, args)
        print(f"[{idx}/{len(case_dirs)}] {case_dir} -> {output_dir}")


if __name__ == "__main__":
    main()
