# helm-charts tools

## `sync_charts_release.py`

Packages `foundry-vtt` from `foundry-*` tags in the charts repo into `incubator/`.

```bash
cd /path/to/helm-charts
python3 tools/sync_charts_release.py release --tag foundry-14.368
python3 tools/sync_charts_release.py release --all
```

Defaults (auto-detected when the script lives in `helm-charts/tools/`):

- **charts repo:** sibling `../charts`
- **helm-charts:** parent of `tools/` (this repo)

Override paths:

```bash
python3 tools/sync_charts_release.py release --all \
  --charts-repo /path/to/charts \
  --helm-charts /path/to/helm-charts
```
