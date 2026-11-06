import numpy as np
import pandas as pd
from RCAEval.io.time_series import drop_constant


def fine2coarse_addup(fine_ranks):
    _coarse_ranks = [(i.split("_")[0], s) for i, s in fine_ranks]
    score_dict = {}
    for i, s in _coarse_ranks:
        if i in score_dict:
            score_dict[i] += s
        else:
            score_dict[i] = s
    coarse_ranks = [(i, s) for i, s in score_dict.items()]
    coarse_ranks = sorted(coarse_ranks, key=lambda x: x[1], reverse=True)
    return coarse_ranks


def fine2coarse_highest(fine_ranks):
    if not fine_ranks:
        return []
    fine_ranks = sorted(fine_ranks, key=lambda x: x[1], reverse=True)
    _coarse_ranks = [(i.split("_")[0], s) for i, s in fine_ranks]
    coarse_ranks = [_coarse_ranks[0]]
    for svc, score in _coarse_ranks[1:]:
        if svc not in [i for i, _ in coarse_ranks]:
            coarse_ranks.append((svc, score))
    coarse_ranks = [(i, s / sum([s for _, s in coarse_ranks])) for i, s in coarse_ranks]
    return coarse_ranks


def _canonical_service(name):
    # Map service aliases from different modalities to a common service name.
    return "frontend" if name == "frontendservice" else name


def _service_from_node(node_name):
    # Extract the service name from a fine-grained node and normalize its alias.
    return _canonical_service(str(node_name).split("_", 1)[0])


def _robust_deviation_scores(normal_values, anomal_values, signed=False):
    # Use median/MAD to score deviations in the anomalous window from the normal window.
    # Keep signed deviations when signed=True; otherwise take absolute values.
    if normal_values.size == 0 or anomal_values.size == 0:
        return np.array([])
    normal_values = np.asarray(normal_values, dtype=float)
    anomal_values = np.asarray(anomal_values, dtype=float)
    median = np.nanmedian(normal_values)
    mad = np.nanmedian(np.abs(normal_values - median))
    if np.isnan(mad) or mad < 1e-9:
        std = np.nanstd(normal_values)
        scale = std if std > 1e-9 else 1.0
    else:
        # 1.4826 scales MAD to a standard deviation estimate under normality.
        scale = 1.4826 * mad
    scores = (anomal_values - median) / scale
    if not signed:
        scores = np.abs(scores)
    neginf = -1e9 if signed else 1e9
    return np.nan_to_num(scores, nan=0.0, posinf=1e9, neginf=neginf)


def trend_statistics(
    normal_values, anomal_values, onset_alpha=0.5, severity_quantile=0.8
):
    """Extract trend-based anomaly statistics from a metric.
    - severity_score: anomaly magnitude.
    - persistence: mean aligned deviation divided by its severity quantile,
      clipped to [0, 1].
    - onset_score: earlier anomalies receive higher scores.
    """
    default_value = {"severity_score": 0.0, "persistence": 0.0, "onset_score": 0.0}
    signed = _robust_deviation_scores(normal_values, anomal_values, signed=True)
    if signed.size == 0:
        return default_value
    # After direction alignment, use a high quantile for severity so that a single spike
    # does not completely dominate the score.
    series = pd.Series(signed, dtype=float)
    # Align the overall direction to be positive: sustained increases and decreases
    # can both be anomalous trends.
    direction = np.sign(np.sum(series))
    if direction == 0:
        direction = 1.0
    aligned = np.maximum(direction * series, 0.0)
    horizon = len(aligned)
    peak = float(np.quantile(aligned, severity_quantile)) if horizon > 0 else 0.0
    if peak <= 1e-12:
        return default_value
    mean_level = float(np.mean(aligned)) if horizon > 0 else 0.0
    persistence = float(np.clip(mean_level / (peak + 1e-12), 0.0, 1.0))
    onset_score = 0.0
    above = np.where(aligned >= onset_alpha * peak)[0]
    if above.size > 0:
        onset_idx = int(above[0])
        onset_score = float((horizon - onset_idx) / horizon)
    return {
        "severity_score": max(0.0, peak),
        "persistence": persistence,
        "onset_score": onset_score,
    }


def _compute_service_anchor_features_trend(
    metric, inject_time, onset_alpha=0.5, severity_quantile=0.8
):
    normal_metric = metric[metric["time"] < inject_time]
    anomal_metric = metric[metric["time"] >= inject_time]
    service_features = {}
    for col in metric.columns:
        if col == "time":
            continue
        # Metric columns usually have the form service_metricName; take the service
        # name before the last underscore.
        service = _canonical_service(col.rsplit("_", 1)[0])
        trend_stats = trend_statistics(
            normal_metric[col].to_numpy(),
            anomal_metric[col].to_numpy(),
            onset_alpha=onset_alpha,
            severity_quantile=severity_quantile,
        )
        # A service may have multiple metrics; accumulate their trend evidence.
        # setdefault does not reinitialize an existing service entry.
        feature = service_features.setdefault(
            service,
            {"metric_severity": 0.0, "metric_persistence": 0.0, "metric_onset": 0.0},
        )
        feature["metric_severity"] += trend_stats["severity_score"]
        feature["metric_persistence"] += trend_stats["persistence"]
        feature["metric_onset"] += trend_stats["onset_score"]
    return service_features


def _normalize_feature_map(feature_map):
    # Max-normalize each service feature to [0, 1] before fusing different units.
    max_value = max(feature_map.values(), default=0.0)
    if max_value <= 0:
        return {key: 0.0 for key in feature_map}
    return {key: value / max_value for key, value in feature_map.items()}


def _compute_metric_anchor_trend_scores(service_features, onset_weight=1.0):
    # Compute service-level anchor scores using metric trend features only.
    first_weight = 1.0
    second_weight = onset_weight
    divisor = max(first_weight + second_weight, 1.0)
    # Normalize each feature across services.
    severity = _normalize_feature_map(
        {svc: feats["metric_severity"] for svc, feats in service_features.items()}
    )
    persistence = _normalize_feature_map(
        {svc: feats["metric_persistence"] for svc, feats in service_features.items()}
    )
    onset = _normalize_feature_map(
        {svc: feats["metric_onset"] for svc, feats in service_features.items()}
    )
    # Compute the fused scores.
    scores = {}
    for svc in service_features:
        first_term = severity.get(svc, 0.0) * persistence.get(svc, 0.0)
        second_term = onset.get(svc, 0.0)
        scores[svc] = (first_term + second_weight * second_term) / divisor
    return scores


def _smooth_metric_frame(metric, window_seconds=15):
    """Apply a trailing rolling median without changing timestamps."""
    if metric.empty:
        return metric
    smoothed = metric.copy()
    value_cols = [col for col in smoothed.columns if col != "time"]
    if value_cols:
        smoothed[value_cols] = (
            smoothed[value_cols].rolling(window=window_seconds, min_periods=1).median()
        )
    return smoothed


def _first_onset_index(traj, min_abs_score=1.0, rel_ratio=0.5):
    # Find the first threshold crossing; use the peak index if none crosses.
    if traj.size == 0:
        return np.inf
    peak = float(np.max(traj))
    if peak <= 0:
        return np.inf
    threshold = max(min_abs_score, rel_ratio * peak)
    hits = np.where(traj >= threshold)[0]
    if hits.size > 0:
        return int(hits[0])
    return int(np.argmax(traj))


def _best_lead_correlation(source_traj, target_traj):
    """Estimate the strongest propagation from source to target at a nonnegative lag.
    Return the mean aligned z-product used by the original EASE:
    sum(src_z[:n-lag] * dst_z[lag:]) / (n-lag).

    Return (best_corr, best_lag):
    - best_corr is the nonnegative propagation strength at the best lag;
    - best_lag is the delay in samples at which best_corr is attained, meaning
      source shows a similar change best_lag samples before target.
    """
    best_corr = 0.0
    best_lag = 0
    n = min(len(source_traj), len(target_traj))
    if n < 3:
        return best_corr, best_lag
    src = np.asarray(source_traj[:n], dtype=float)
    dst = np.asarray(target_traj[:n], dtype=float)
    src_std = np.std(src)
    dst_std = np.std(dst)
    if src_std < 1e-12 or dst_std < 1e-12:
        return 0.0, 0
    src_norm = (src - np.mean(src)) / src_std
    dst_norm = (dst - np.mean(dst)) / dst_std
    xcorr = np.correlate(dst_norm, src_norm, mode="full")
    mid = n - 1
    valid = xcorr[mid:]  # lag >= 0
    correlations = np.array(
        [float(valid[k]) / max(n - k, 1) for k in range(len(valid))]
    )
    best_idx = int(np.argmax(correlations))
    best_corr = float(correlations[best_idx])
    best_lag = best_idx
    return max(0.0, best_corr), best_lag


def _build_service_metric_trajectories(metric_trajectories):
    # Group metric-level anomaly trajectories by service and average their
    # log1p-transformed values; arrays within each group must already have equal length.
    service_to_trajs = {}
    for col, values in metric_trajectories.items():
        service = _service_from_node(col)
        values = np.asarray(values, dtype=float)
        if values.size == 0:
            continue
        service_to_trajs.setdefault(service, []).append(np.log1p(values))
    service_trajs = {}
    for service, traj_list in service_to_trajs.items():
        service_trajs[service] = np.mean(np.vstack(traj_list), axis=0)
    return service_trajs


def _local_explain_order(
    candidate_services,
    pre_refine_order,
    service_trajs,
    base_score_dict,
    onset_beta=0.5,
    onset_tau=1.0,
    lead_weight=1.0,
):
    """Reorder candidate services using local propagation evidence.

    For each observed service, consider the other available services as hypotheses.
    A hypothesis receives more weight when its anomaly starts earlier and its
    leading trajectory better explains the observed service. Multiply this weight
    by the hypothesis's base-score prior. Normalize within each observed service
    and accumulate into mass[hypothesis]. Rank by accumulated explanation mass,
    using the base score only to break ties.
    """
    # There is nothing to reorder when fewer than two services are available.
    available = [svc for svc in candidate_services if svc in service_trajs]
    if len(available) < 2:
        return list(pre_refine_order)
    # Normalize the fused service scores into a prior for each service's own
    # suspiciousness.
    base_scores = {
        svc: max(float(base_score_dict.get(svc, 0.0)), 0.0)
        for svc in candidate_services
    }
    total = sum(base_scores.values())
    if total <= 1e-12:
        base_norm = {svc: 1.0 / max(len(base_scores), 1) for svc in base_scores}
    else:
        base_norm = {svc: val / total for svc, val in base_scores.items()}
    onset_map = {
        svc: _first_onset_index(
            service_trajs[svc], min_abs_score=onset_tau, rel_ratio=onset_beta
        )
        for svc in available
    }
    # mass[hypothesis] accumulates its probability mass for explaining other
    # observed services.
    mass = {svc: 0.0 for svc in candidate_services}
    for observed in available:
        weights = {}
        z = 0.0
        for hypothesis in available:
            if hypothesis == observed:
                continue
            src = np.asarray(service_trajs[hypothesis], dtype=float)
            dst = np.asarray(service_trajs[observed], dtype=float)
            n = min(len(src), len(dst))
            src = src[:n]
            dst = dst[:n]
            corr, lag = _best_lead_correlation(src, dst)
            src_onset = onset_map.get(hypothesis, np.inf)
            dst_onset = onset_map.get(observed, np.inf)
            if np.isfinite(src_onset) and np.isfinite(dst_onset) and n > 1:
                onset_gap = float(dst_onset - src_onset)
                lead = max(0.0, min(1.0, onset_gap / float(n - 1)))
            else:
                lead = 0.0
            # corr measures propagation strength after lead alignment; lead is the
            # onset-time prior.
            score = (max(float(corr), 0.0) + lead_weight * lead) / (1.0 + lead_weight)
            score = max(score, 1e-8)
            # Multiply by the source's normalized multimodal base score as the prior.
            weights[hypothesis] = score * max(
                float(base_norm.get(hypothesis, 0.0)), 1e-8
            )
            z += weights[hypothesis]
        z += 1e-12
        for hypothesis, weight in weights.items():
            mass[hypothesis] += weight / z
    # base_norm already acts as the hypothesis prior in responsibility assignment;
    # rank by accumulated explanation mass.
    final_order = sorted(
        candidate_services,
        key=lambda svc: (mass.get(svc, 0.0), base_norm.get(svc, 0.0)),
        reverse=True,
    )
    return final_order


def _metric_anomaly_trajectories(normal_df, anomal_df):
    """Build robust z-score trajectories for anomalous metrics using the normal distribution."""
    trajectories = {}
    for col in normal_df.columns:
        if col not in anomal_df.columns:
            continue
        a = normal_df[col].to_numpy()
        b = anomal_df[col].to_numpy()
        if a.size == 0 or b.size == 0:
            continue
        trajectories[col] = _robust_deviation_scores(a, b)
    return trajectories


def _score_columns_by_robust_shift(normal_df, anomal_df):
    """Compute column-level robust shift scores for the anomalous window relative to normal."""
    common_cols = normal_df.columns.intersection(anomal_df.columns)
    if normal_df.empty or anomal_df.empty or common_cols.empty:
        return []
    column_scores = []
    for col in common_cols:
        a = normal_df[col].to_numpy()
        b = anomal_df[col].to_numpy()
        score = float(np.max(_robust_deviation_scores(a, b)))
        column_scores.append((col, score))
    total = sum(score for _, score in column_scores)
    if total == 0:
        uniform = 1.0 / len(column_scores)
        return [(name, uniform) for name, _ in column_scores]
    return [(name, score / total) for name, score in column_scores]


def ease(data, inject_time=None, dataset=None, **kwargs):
    """CLI entry point: EASE metric anchor + multimodal fusion + local explain.
    Sampling, smoothing, weights, and thresholds are passed explicitly through
    kwargs; omitted values use the original algorithm defaults. The severity
    quantile and metric base coefficient participate in the main computation
    and can also be varied through kwargs.
    """
    # Retain the sample_s/smooth_s interface for explicit time-processing parameters.
    sample_s = int(kwargs.get("sample_s", 1))
    smooth_s = int(kwargs.get("smooth_s", 15))
    metric_onset_weight = float(kwargs.get("metric_onset_weight", 1.0))
    lead_weight = float(kwargs.get("lead_weight", 1.0))
    onset_alpha = float(kwargs.get("alpha", 0.5))
    onset_beta = float(kwargs.get("beta", 0.5))
    onset_tau = float(kwargs.get("tau", 1.0))
    severity_quantile = float(kwargs.get("severity_quantile", 0.8))
    metric_base_weight = float(kwargs.get("metric_base_weight", 1.0))
    log_weight = float(kwargs.get("log_weight", 1.0))
    trace_latency_weight = float(kwargs.get("trace_latency_weight", 1.0))
    trace_error_weight = float(kwargs.get("trace_error_weight", 1.0))
    if sample_s < 1 or smooth_s < 1:
        raise ValueError("sample_s and smooth_s must be positive integers")
    if metric_onset_weight < 0 or lead_weight < 0:
        raise ValueError("metric_onset_weight and lead_weight must be non-negative")
    if onset_alpha < 0 or onset_beta < 0 or onset_tau < 0:
        raise ValueError("alpha, beta and tau must be non-negative")
    if not 0 < severity_quantile < 1:
        raise ValueError("severity_quantile must be strictly between 0 and 1")
    if any(
        weight <= 0
        for weight in (
            metric_base_weight,
            log_weight,
            trace_latency_weight,
            trace_error_weight,
        )
    ):
        raise ValueError("modality weights must be positive")
    # Keep the original 1-second metrics for EASE's metric anchor and local explain.
    metric_raw = data["metric"].copy()
    logts = data.get("logts")
    traces_err = data.get("tracets_err")
    traces_lat = data.get("tracets_lat")
    logts = logts.copy() if logts is not None else pd.DataFrame()
    traces_err = traces_err.copy() if traces_err is not None else pd.DataFrame()
    traces_lat = traces_lat.copy() if traces_lat is not None else pd.DataFrame()
    has_traces = (
        traces_err is not None
        and len(traces_err) > 0
        and traces_lat is not None
        and len(traces_lat) > 0
    )
    # Keep raw values when the smoothing window is 1; then sample at equal
    # intervals on the original 1-second grid.
    metric_raw_smoothed = (
        metric_raw
        if smooth_s == 1
        else _smooth_metric_frame(metric_raw, window_seconds=smooth_s)
    )
    metric_raw_smoothed = metric_raw_smoothed.iloc[::sample_s, :].reset_index(drop=True)
    normal_metric_raw_smoothed = metric_raw_smoothed[
        metric_raw_smoothed["time"] < inject_time
    ]
    anomal_metric_raw_smoothed = metric_raw_smoothed[
        metric_raw_smoothed["time"] >= inject_time
    ]
    # logts is an input modality, not diagnostic logging. If absent, keep only
    # time so the pipeline can still run.
    if logts is None or logts.empty:
        logts = pd.DataFrame({"time": metric_raw["time"].values})
    logts = drop_constant(logts)
    normal_logts = logts[logts["time"] < inject_time].drop(
        columns=["time"], errors="ignore"
    )
    anomal_logts = logts[logts["time"] >= inject_time].drop(
        columns=["time"], errors="ignore"
    )
    # Forward-fill traces, fill missing values with 0, and remove constant columns.
    if has_traces:
        traces_err = drop_constant(traces_err.ffill().fillna(0))
        traces_lat = drop_constant(traces_lat.ffill().fillna(0))
        normal_traces_err = traces_err[traces_err["time"] < inject_time].drop(
            columns=["time"], errors="ignore"
        )
        anomal_traces_err = traces_err[traces_err["time"] >= inject_time].drop(
            columns=["time"], errors="ignore"
        )
        normal_traces_lat = traces_lat[traces_lat["time"] < inject_time].drop(
            columns=["time"], errors="ignore"
        )
        anomal_traces_lat = traces_lat[traces_lat["time"] >= inject_time].drop(
            columns=["time"], errors="ignore"
        )
    else:
        traces_err = pd.DataFrame()
        traces_lat = pd.DataFrame()
        normal_traces_err = anomal_traces_err = pd.DataFrame()
        normal_traces_lat = anomal_traces_lat = pd.DataFrame()
    # Build service-level metric anchor scores as anomaly priors and fusion inputs.
    service_features = _compute_service_anchor_features_trend(
        metric=metric_raw_smoothed,
        inject_time=inject_time,
        onset_alpha=onset_alpha,
        severity_quantile=severity_quantile,
    )
    # Normalize and aggregate the scores.
    metric_anchor_scores = _compute_metric_anchor_trend_scores(
        service_features, onset_weight=metric_onset_weight
    )
    # Metric-level trajectories feed local explain; ranking priors come from
    # the metric anchor.
    metric_column_trajectories = _metric_anomaly_trajectories(
        normal_metric_raw_smoothed, anomal_metric_raw_smoothed
    )
    log_scores = _score_columns_by_robust_shift(normal_logts, anomal_logts)
    trace_err_scores = []
    trace_lat_scores = []
    if has_traces:
        trace_err_scores = _score_columns_by_robust_shift(
            normal_traces_err, anomal_traces_err
        )
        trace_lat_scores = _score_columns_by_robust_shift(
            normal_traces_lat, anomal_traces_lat
        )
    # Aggregate column-level scores into service-level inputs for multimodal fusion.
    svc_log_scores = fine2coarse_highest(log_scores)
    svc_trace_err_scores = fine2coarse_addup(trace_err_scores)
    svc_trace_lat_scores = fine2coarse_addup(trace_lat_scores)
    # Match TORAI's trace service aliases to the metric/log service names.
    svc_trace_err_scores = [
        (_canonical_service(svc), score) for svc, score in svc_trace_err_scores
    ]
    svc_trace_lat_scores = [
        (_canonical_service(svc), score) for svc, score in svc_trace_lat_scores
    ]
    # Multimodal service table: metric/log/trace_lat/trace_err.
    m = pd.DataFrame(
        {
            "metric": pd.Series(metric_anchor_scores, dtype=float),
            "log": pd.Series(dict(svc_log_scores), dtype=float),
            "trace_lat": pd.Series(dict(svc_trace_lat_scores), dtype=float),
            "trace_err": pd.Series(dict(svc_trace_err_scores), dtype=float),
        }
    ).fillna(0)
    if m.empty:
        fallback = sorted(
            metric_anchor_scores.items(), key=lambda x: x[1], reverse=True
        )
        return {"ranks": [f"{svc}_A" for svc, _ in fallback]}
    service_list = m.index.to_list()
    service_metric_trajectories = _build_service_metric_trajectories(
        metric_column_trajectories
    )
    if len(service_list) == 1:
        service_ranks = [service_list[0]]
    else:
        # Multimodal fusion uses the default multiplicative mode.
        log_map = dict(svc_log_scores)
        trace_lat_map = dict(svc_trace_lat_scores)
        trace_err_map = dict(svc_trace_err_scores)
        multimodal_base = {}
        for svc in service_list:
            m = metric_anchor_scores.get(svc, 0.0)
            # Multiplicative fusion.
            l = log_map.get(svc, 0.0)
            tl = trace_lat_map.get(svc, 0.0)
            te = trace_err_map.get(svc, 0.0)
            auxiliary = l + tl + te
            if (
                metric_base_weight,
                log_weight,
                trace_latency_weight,
                trace_error_weight,
            ) == (1.0, 1.0, 1.0, 1.0):
                # Preserve the original arithmetic and default rankings exactly.
                multimodal_base[svc] = (m + m * auxiliary) / 4.0
            else:
                weight_sum = (
                    metric_base_weight
                    + log_weight
                    + trace_latency_weight
                    + trace_error_weight
                )
                multimodal_base[svc] = (
                    m
                    * (
                        metric_base_weight
                        + log_weight * l
                        + trace_latency_weight * tl
                        + trace_error_weight * te
                    )
                    / weight_sum
                )
        # Sort fallback_order by the fused multimodal scores.
        fallback_order = [
            svc
            for svc, _ in sorted(
                multimodal_base.items(), key=lambda x: x[1], reverse=True
            )
        ]
        service_ranks = _local_explain_order(
            candidate_services=service_list,
            pre_refine_order=fallback_order,
            service_trajs=service_metric_trajectories,
            base_score_dict=multimodal_base,
            onset_beta=onset_beta,
            onset_tau=onset_tau,
            lead_weight=lead_weight,
        )
    deduped = []
    for svc in service_ranks:
        if svc not in deduped:
            deduped.append(svc)
    return {"ranks": [f"{svc}_A" for svc in deduped]}
