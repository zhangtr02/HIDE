# HIDE

**HIDE: Hierarchical Isolation via Documentation and Evidence for Rustc Bugs** ranks suspicious source files in the Rust compiler. It combines layered documentation of crate, module, and file responsibilities with hierarchical bug isolation, scoped candidate competition, and evidence-guided code slicing.

## Repository Structure

| Path | Description |
| --- | --- |
| `src/` | Documentation generation, evidence extraction, and bug isolation. |
| `config/config.yaml` | Model, path, and pipeline settings. |
| `rustcbugs.csv`, `rustcbugs/`, `issues/` | Evaluated bug IDs, failure programs, and issue descriptions. |
| `logs/`, `query_traces/`, `evidence/` | Compiler output and extracted bug evidence. |
| `docs_file/`, `docs_module/`, `docs_crate/` | Generated documentation at each level. |
| `groundtruth/` | File-level ground truth derived from fixing pull requests. |
| `results/`, `ablation_study/` | Main experiment and ablation results. |
| `baselines/`, `gcc-cbi/` | Baseline implementations and GCC transfer experiments. |

The evaluation dataset contains 160 rustc bugs: 94 internal compiler errors, 46 completeness bugs, and 20 soundness bugs. It is derived from the dataset in *An Empirical Study of Bugs in the rustc Compiler*.

## Quick Start

Run from the repository root with Python 3.10+, Git, and rustup. Install the Python dependencies, configure the model in `config/config.yaml`, and provide an API key:

```bash
python3 -m pip install openai PyYAML
export OPENAI_API_KEY="YOUR_API_KEY"
git clone https://github.com/rust-lang/rust.git rust
```

The rustc checkout must contain the revision for the bug being analyzed. To run one bug using the prepared documentation and evidence:

```bash
python3 -m src --bug-id 118990 --report-dir results/local/reports
python3 -m src.generate_result --bug-id 118990 \
  --report-dir results/local/reports \
  --output results/local/result.json
```

To generate documentation and evidence for a bug before running isolation:

```bash
python3 -m src.docgen --bug-id 118990
python3 -m src.evidence --bug-id 118990
```

## Results

`results/` contains per-run reports and aggregate evaluation results for HIDE and the baselines. Each `reports/<bug-id>.json` contains ranked suspicious files; the corresponding `result.json` summarizes file-level accuracy and efficiency. Ablation summaries are under `ablation_study/<variant>/result.json`.
