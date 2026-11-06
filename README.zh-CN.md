# EASE-RCA

[English](README.md) | 简体中文

论文 [**EASE-RCA: Evidence-aware service explanation with temporal responsibility for trace-topology-free microservice root cause analysis**](https://doi.org/10.1016/j.jss.2026.113166) 的代码实现，发表于 *Journal of Systems and Software*，2026。

EASE-RCA 结合指标异常证据、日志与 Trace 时间序列以及时序责任分配，对根因服务进行排序，不依赖服务调用图或 span 级调用关系。以下脚本支持 RE2 主实验、RE1 仅指标实验和 RE3 代码故障实验。

仓库内的 `RCAEval/` 基于 [RCAEval](https://github.com/phamquiluan/RCAEval) v1.12.0，commit 为 [`90b63a46ac1e969f1fa9015318b51e455c62f8c4`](https://github.com/phamquiluan/RCAEval/commit/90b63a46ac1e969f1fa9015318b51e455c62f8c4)，并接入 EASE。算法实现见 [ease.py](RCAEval/RCAEval/e2e/ease.py)。

## 🛠️ 安装

环境：Linux、Python 3.12、CPU。在仓库根目录执行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install --no-deps -e ./RCAEval
```

## 📥 数据

三个数据集均包含 Online Boutique（OB）、Sock Shop（SS）和 Train Ticket（TT）。

| 数据集 | 输入 | 案例数 | 运行时的数据集名称 |
| --- | --- | ---: | --- |
| RE2 | TORAI 预处理后的指标、日志与 Trace 时间序列；SS 无 Trace | 270 | `torai-ob`、`torai-ss`、`torai-tt` |
| RE1 | 仅指标 | 375 | `re1-ob`、`re1-ss`、`re1-tt` |
| RE3 | 指标及转换后的日志与 Trace 时间序列；SS 无可用 Trace | 90 | `re3-ob`、`re3-ss`、`re3-tt` |

从 [TORAI 数据发布页](https://doi.org/10.6084/m9.figshare.31925976) 下载 **RE2**：

```bash
mkdir -p RCAEval/data
curl -fL --retry 3 -o RCAEval/data/torai-data.zip \
  "https://ndownloader.figshare.com/files/63402423"
unzip -q -n RCAEval/data/torai-data.zip -d RCAEval/data/
```

解压后得到 `RCAEval/data/torai-OB`、`torai-SS` 和 `torai-TT`。每个案例包含 `simple_metrics.csv`、`logts.csv`、`inject_time.txt`；OB 和 TT 还包含 `tracets_err.csv`、`tracets_lat.csv`。

从 [RCAEval Zenodo 发布页](https://zenodo.org/records/14590730) 下载 **RE1**：

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

每个 RE1 案例包含 `data.csv` 和 `inject_time.txt`。已有数据时，使用现有目录或软链接即可。

从同一 [RCAEval Zenodo 发布页](https://zenodo.org/records/14590730) 下载 **RE3**，然后转换原始日志与 Trace：

```bash
mkdir -p RCAEval/data/RE3
for system in OB SS TT; do
  curl -fL --retry 3 -o "RCAEval/data/RE3/RE3-${system}.zip" \
    "https://zenodo.org/records/14590730/files/RE3-${system}.zip?download=1"
  unzip -q -n "RCAEval/data/RE3/RE3-${system}.zip" -d RCAEval/data/RE3/
  python scripts/re3_to_torai_timeseries.py \
    --input-root "RCAEval/data/RE3/RE3-${system}"
done
```

转换脚本改编自论文的 RE3 预处理脚本，生成 TORAI 格式的 CSV：按 15 秒分桶，每个案例保留频次最高的 50 个“服务—日志模板”组合（TT 为 200 个）和 80 个“服务—Trace 操作”组合。原始输入及指标采样保持不变。

## 🚀 运行

激活环境后，在仓库根目录执行：

```bash
bash scripts/run.sh re2   # 主实验：270 例
bash scripts/run.sh re1   # 仅指标实验：375 例
bash scripts/run.sh re3   # 代码故障实验：90 例
# 或一次运行三个数据集：
bash scripts/run.sh all
```

脚本依次运行三个系统并生成汇总。默认使用 `--length 20 --tdelta 0`，即总计 20 分钟、故障注入前后各至多 10 分钟的窗口；记录不足时使用已有样本。指标采样间隔为 1 秒，采用 15 个样本的滑动中位数平滑；日志与 Trace 序列按 15 秒聚合。

单独运行一个系统：

```bash
cd RCAEval
python main.py --method ease --dataset torai-ob --length 20 --tdelta 0
cd ..
```

相较论文原始输入，当前 RCAEval 排除了重复时间列 `time.1`，少量 RE1-OB 排名因此改善，Top-1 准确率不变。

## 📂 输出

- 逐例预测：`temp/output/ease/{dataset}/results/`。
- 汇总：`temp/results/{re1,re2,re3}/`，包括 `README.md`、`summary.csv`、`by_fault.csv`、`by_service_fault.csv` 和 `cases.csv`。

评测采用服务级 **AC@1–5**、**Avg@5**（AC@1 至 AC@5 的均值）和 **MRR**（完整服务排名中根因名次倒数的均值）。数据库容器归并到所属服务，同一服务只计一次。

完整运行后，可单独重新汇总：

```bash
python summarize_results.py --suite re2
python summarize_results.py --suite re1
python summarize_results.py --suite re3
```

重复运行会覆盖对应的预测和汇总。数据与 `temp/` 输出均不纳入 Git。

RCAEval 及第三方许可证保留在 [RCAEval/LICENSE](RCAEval/LICENSE) 和 [RCAEval/LICENSES](RCAEval/LICENSES)。

## 📖 引用

如果我们的工作或代码对你的研究有所帮助，欢迎引用以下论文：

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
