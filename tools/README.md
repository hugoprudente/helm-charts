# helm-charts tools

Release automation lives in the **foundry workspace**, not in this folder:

**[`../../tools/sync_charts_release.py`](../../tools/sync_charts_release.py)**

```bash
cd /path/to/foundry

# One tag
python3 tools/sync_charts_release.py release --tag foundry-14.368

# Every release tag in charts (foundry-*, foundryvtt-*, foundry0.x) → .tgz + full index
python3 tools/sync_charts_release.py release --all
```

Packages and `index.yaml` are written under **`helm-charts/incubator/`**.

Tag patterns match **`foundry-14.368`**, **`foundryvtt-…`** (if you add them), and legacy **`foundry0.7.5`**. There are no `foundryvtt-*` tags in charts today—only `foundry-*` and `foundry0.7.5`.
