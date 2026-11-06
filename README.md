# EASE-RCA

English | [简体中文](README.zh-CN.md)

Implementation of [**EASE-RCA: Evidence-aware service explanation with temporal responsibility for trace-topology-free microservice root cause analysis**](https://doi.org/10.1016/j.jss.2026.113166), *Journal of Systems and Software*, 2026.

EASE-RCA ranks root-cause services using metric anomaly evidence, auxiliary log/trace time series, and temporal responsibility assignment. It does not require service call graphs or span-level call relations. The scripts below reproduce the RE2 main experiment and RE1 metric-only evaluation.

The bundled `RCAEval/` is based on [RCAEval](https://github.com/phamquiluan/RCAEval) v1.12.0, commit [`90b63a46ac1e969f1fa9015318b51e455c62f8c4`](https://github.com/phamquiluan/RCAEval/commit/90b63a46ac1e969f1fa9015318b51e455c62f8c4), with EASE integration. The method is implemented in [ease.py](RCAEval/RCAEval/e2e/ease.py).

## 🛠️ Installation

Linux, Python 3.12, CPU. Run the following from the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install --no-deps -e ./RCAEval
```

## 📥 Data

Both suites cover Online Boutique (OB), Sock Shop (SS), and Train Ticket (TT).

| Suite | Input | Cases | Runner dataset names |
| --- | --- | ---: | --- |
| RE2 | TORAI-preprocessed metrics, logs, and trace time series; SS has no traces | 270 | `torai-ob`, `torai-ss`, `torai-tt` |
| RE1 | Metrics only | 375 | `re1-ob`, `re1-ss`, `re1-tt` |

Download **RE2** from the [TORAI data release](https://doi.org/10.6084/m9.figshare.31925976):

```bash
mkdir -p RCAEval/data
curl -fL --retry 3 -o RCAEval/data/torai-data.zip \
  "https://ndownloader.figshare.com/files/63402423"
unzip -q -n RCAEval/data/torai-data.zip -d RCAEval/data/
```

This creates `RCAEval/data/torai-OB`, `torai-SS`, and `torai-TT`. Each case contains `simple_metrics.csv`, `logts.csv`, and `inject_time.txt`; OB and TT also contain `tracets_err.csv` and `tracets_lat.csv`.

Download **RE1** from the [RCAEval Zenodo release](https://zenodo.org/records/14590730):

```bash
mkdir -p RCAEval/data/RE1
for system in OB SS TT; do
  curl -fL --retry 3 -o "RCAEval/data/RE1/RE1-${system}.zip" \
    "https://zenodo.org/records/14590730/files/RE1-${system}.zip?download=1"
  unzip -q -n "RCAEval/data/RE1/RE1-${system}.zip" -d RCAEval/data/RE1/
done
ln -s RE1/RE1-OB RCAEval/data/online-boutique
ln -s RE1/RE1-SS RCAEval/data/sock-shop-2
ln -s RE1/RE1-TT RCAEval/data/train-ticket
```

Each RE1 case contains `data.csv` and `inject_time.txt`. If the data is already present, use the existing directories or symlinks.

## 🚀 Run

From the repository root, with the environment activated:

```bash
bash scripts/run.sh re2   # Main experiment: 270 cases
bash scripts/run.sh re1   # Metric-only evaluation: 375 cases
# Or run both suites:
bash scripts/run.sh all
```

The script runs EASE on all three systems and generates the summary. It uses `--length 20 --tdelta 0`: a total window of 20 minutes, up to 10 minutes on each side of injection. Shorter recordings use the available samples. Metrics use 1-second sampling and a trailing median window of 15 samples; log/trace series use 15-second buckets.

To run one dataset directly:

```bash
cd RCAEval
python main.py --method ease --dataset torai-ob --length 20 --tdelta 0
cd ..
```

Compared with the paper's original inputs, the bundled RCAEval excludes duplicated `time.1` timestamp columns, slightly improving some RE1-OB ranks while leaving Top-1 accuracy unchanged.

## 📂 Output

- Predictions: `temp/output/ease/{dataset}/results/`.
- Summaries: `temp/results/{re1,re2}/`, including `README.md`, `summary.csv`, `by_fault.csv`, `by_service_fault.csv`, and `cases.csv`.

Scores are service-level **AC@1–5**, **Avg@5** (mean of AC@1 through AC@5), and **MRR** (mean reciprocal rank over the complete service ranking). Database containers are folded into their services, and duplicate services are counted once.

To regenerate summaries after a complete run:

```bash
python summarize_results.py --suite re2
python summarize_results.py --suite re1
```

Repeated runs overwrite the corresponding predictions and summaries. Data and `temp/` outputs are excluded from Git.

RCAEval and third-party licenses are retained in [RCAEval/LICENSE](RCAEval/LICENSE) and [RCAEval/LICENSES](RCAEval/LICENSES).

## 📖 Citation

If you find our work or code useful for your research, please consider citing our paper:

```bibtex
@article{sun2026ease_rca,
  title   = {{EASE-RCA}: Evidence-aware service explanation with temporal responsibility for trace-topology-free microservice root cause analysis},
  author  = {Sun, Shang and Teng, Fei and Zheng, Danyang and Xu, Jie},
  journal = {Journal of Systems and Software},
  year    = {2026},
  pages   = {113166},
  doi     = {10.1016/j.jss.2026.113166}
}
```
