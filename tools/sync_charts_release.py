#!/usr/bin/env python3
"""Package foundry-vtt from hugoprudente/charts tags into this GitHub Pages repo."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

def _default_paths() -> tuple[Path, Path]:
    """Resolve charts source repo and helm-charts (GitHub Pages) repo from script location."""
    script = Path(__file__).resolve()
    tools_dir = script.parent
    # .../helm-charts/tools/sync_charts_release.py
    if (tools_dir.parent / "incubator").is_dir():
        helm_charts = tools_dir.parent
        charts = helm_charts.parent / "charts"
        return charts, helm_charts
    # .../foundry/tools/sync_charts_release.py (or any tools/ next to helm-charts/)
    workspace = tools_dir.parent
    helm_candidate = workspace / "helm-charts"
    if (helm_candidate / "incubator").is_dir():
        return workspace / "charts", helm_candidate
    return workspace / "charts", workspace / "helm-charts"


_DEFAULT_CHARTS, _DEFAULT_HELM = _default_paths()
DEFAULT_CHARTS_REPO = _DEFAULT_CHARTS
DEFAULT_HELM_CHARTS = _DEFAULT_HELM
CHART_PATH_IN_SOURCE = "incubator/foundry-vtt"
CHART_NAME = "foundry-vtt"
# Release tags in hugoprudente/charts (one tag → one packaged chart).
RELEASE_TAG_PATTERNS = (
    re.compile(r"^foundry-.+"),
    re.compile(r"^foundryvtt-.+"),
    re.compile(r"^foundry\d"),  # legacy e.g. foundry0.7.5
)
REPO_PAGE_URL = "https://hugoprudente.github.io/helm-charts/incubator"
SOURCE_REPO = "https://github.com/hugoprudente/charts"


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=True)


def git_output(cmd: list[str], *, cwd: Path) -> str:
    result = run(["git", *cmd], cwd=cwd)
    return result.stdout.strip()


def ensure_charts_repo(path: Path, remote: str) -> None:
    if path.is_dir() and (path / ".git").is_dir():
        run(["git", "fetch", "--tags", "origin"], cwd=path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--filter=blob:none", remote, str(path)])


def _tag_version_body(tag: str) -> str:
    for prefix in ("foundryvtt-", "foundry-", "foundry"):
        if tag.startswith(prefix):
            return tag[len(prefix) :]
    return tag


def list_release_tags(charts_repo: Path) -> list[str]:
    out = git_output(["tag", "-l"], cwd=charts_repo)
    tags: set[str] = set()
    for line in out.splitlines():
        name = line.strip()
        if any(p.match(name) for p in RELEASE_TAG_PATTERNS):
            tags.add(name)
    return sorted(tags, key=_tag_sort_key)


def _tag_sort_key(tag: str) -> tuple:
    """Sort foundry-9.280 before foundry-10.303."""
    body = _tag_version_body(tag)
    parts: list[tuple[int, str]] = []
    for piece in re.split(r"[.\-]", body):
        if piece.isdigit():
            parts.append((0, int(piece)))
        else:
            parts.append((1, piece))
    return tuple(parts)


def chart_version_at_ref(charts_repo: Path, ref: str) -> str:
    raw = git_output(["show", f"{ref}:{CHART_PATH_IN_SOURCE}/Chart.yaml"], cwd=charts_repo)
    for line in raw.splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"no version: in Chart.yaml at {ref}")


def foundry_version_at_ref(charts_repo: Path, ref: str) -> str | None:
    """Return foundryvtt.version when present (missing on pre-Felddy legacy charts)."""
    raw = git_output(["show", f"{ref}:{CHART_PATH_IN_SOURCE}/values.yaml"], cwd=charts_repo)
    in_block = False
    for line in raw.splitlines():
        if line.startswith("foundryvtt:"):
            in_block = True
            continue
        if in_block and line and not line.startswith(" "):
            break
        if in_block and line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def foundry_version_label(charts_repo: Path, tag: str) -> str:
    found = foundry_version_at_ref(charts_repo, tag)
    if found:
        return found
    body = _tag_version_body(tag)
    return body if body else "unknown"


def is_modern_chart_version(chart_version: str) -> bool:
    """Track-style chart versions (9.280.0, 13.351.1), not ancient 0.2.x / 0.3."""
    head = chart_version.split("-", 1)[0].split(".", 1)[0]
    return head.isdigit() and int(head) >= 9


def export_chart_tree(charts_repo: Path, ref: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "archive", "--format=tar", ref, CHART_PATH_IN_SOURCE],
        cwd=charts_repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    extract_root = dest.parent
    subprocess.run(
        ["tar", "-xf", "-", "-C", str(extract_root)],
        input=proc.stdout,
        check=True,
    )
    archived = extract_root / CHART_PATH_IN_SOURCE
    if not archived.is_dir():
        raise SystemExit(f"git archive did not produce {archived}")
    shutil.move(str(archived), str(dest))


def package_chart(chart_dir: Path, incubator_dir: Path) -> Path:
    incubator_dir.mkdir(parents=True, exist_ok=True)
    result = run(
        ["helm", "package", str(chart_dir), "-d", str(incubator_dir)],
        check=True,
    )
    # helm prints "Successfully packaged chart and saved it to: ..."
    for line in (result.stdout + result.stderr).splitlines():
        if "saved it to:" in line:
            return Path(line.rsplit(":", 1)[-1].strip())
    tgz = sorted(incubator_dir.glob(f"{CHART_NAME}-*.tgz"), key=lambda p: p.stat().st_mtime)
    if not tgz:
        raise SystemExit("helm package produced no .tgz")
    return tgz[-1]


def reindex(incubator_dir: Path, page_url: str, *, merge: bool = True) -> None:
    index = incubator_dir / "index.yaml"
    merge_args = ["--merge", str(index)] if merge and index.is_file() else []
    run(
        [
            "helm",
            "repo",
            "index",
            str(incubator_dir),
            "--url",
            page_url,
            *merge_args,
        ],
        check=True,
    )


def versions_from_index(index_path: Path) -> list[dict[str, str]]:
    if not index_path.is_file():
        return []
    entries: list[dict[str, str]] = []
    in_entries = False
    current_version: str | None = None
    current_url: str | None = None
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"  {CHART_NAME}:"):
            in_entries = True
            continue
        if in_entries and line and not line.startswith(" "):
            break
        if not in_entries:
            continue
        if line.startswith("  - apiVersion:"):
            if current_version:
                entries.append({"version": current_version, "url": current_url or ""})
            current_version = None
            current_url = None
            continue
        if line.startswith("    version:"):
            current_version = line.split(":", 1)[1].strip()
        if line.startswith("    - ") and "foundry-vtt-" in line and ".tgz" in line:
            current_url = line.split("- ", 1)[-1].strip()
    if current_version:
        entries.append({"version": current_version, "url": current_url or ""})
    return entries


def semver_key(version: str) -> tuple:
    """Sort chart versions (handles 9.280.0, 0.9.280-a, 14.368.2)."""
    main, *rest = version.split("-", 1)
    nums: list[tuple[int, str]] = []
    for part in main.split("."):
        nums.append((0, int(part)) if part.isdigit() else (1, part))
    if rest:
        nums.append((2, rest[0]))
    return tuple(nums)


def write_readme(helm_root: Path, incubator_dir: Path, page_url: str) -> None:
    index_path = incubator_dir / "index.yaml"
    versions = versions_from_index(index_path)
    seen: set[str] = set()
    unique: list[str] = []
    for item in sorted(versions, key=lambda x: semver_key(x["version"]), reverse=True):
        v = item["version"]
        if v in seen:
            continue
        seen.add(v)
        unique.append(v)

    latest_lines = "\n".join(f"- `{v}`" for v in unique[:20])
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    readme = f"""# helm-charts

GitHub Pages Helm repository for [Foundry VTT](https://foundryvtt.com) on Kubernetes.

## Quick reference (for humans and agents)

| Field | Value |
| --- | --- |
| Repository URL | `{page_url}` |
| Chart name | `{CHART_NAME}` |
| Source charts | [{SOURCE_REPO}]({SOURCE_REPO}) (`incubator/foundry-vtt`) |
| Release refs | Git tags `foundry-*` on `foundryvtt/*` branches in the source repo |
| Index | [`incubator/index.yaml`](incubator/index.yaml) |
| Artifact Hub | [foundry-vtt](https://artifacthub.io/packages/search?repo=foundry-vtt) |

## Install

```bash
helm repo add foundry-vtt {page_url}/
helm repo update
helm install my-foundry foundry-vtt/{CHART_NAME} --version <chart-version>
```

Example (replace version with a row from [Latest chart versions](#latest-chart-versions)):

```bash
helm install my-foundry foundry-vtt/{CHART_NAME} --version {unique[0] if unique else "14.368.2"}
```

## Maintainer release

Packages are built from tagged commits in the source charts repository, not from live branches.

```bash
python3 tools/sync_charts_release.py release --tag foundry-14.368
# package every release tag (foundry-*, foundryvtt-*, foundry0.x) and rebuild index
python3 tools/sync_charts_release.py release --all
# only tags missing a .tgz in incubator/
python3 tools/sync_charts_release.py release --all-new
```

(Run from the `foundry` workspace root; outputs go to `helm-charts/incubator/`.)

Requires `git`, `helm`, and a local clone of [hugoprudente/charts]({SOURCE_REPO}) (or pass `--charts-repo`).

## Latest chart versions

<!-- sync_charts_release:versions:start -->
{latest_lines or "- (none packaged yet)"}
<!-- sync_charts_release:versions:end -->

_Generated by `tools/sync_charts_release.py` on {generated}._
"""
    (helm_root / "README.md").write_text(readme, encoding="utf-8")


def write_robots_txt(path: Path) -> None:
    content = """# Public Helm chart index — allow search and LLM crawlers that honor robots.txt.
User-agent: *
Allow: /

User-agent: GPTBot
Allow: /

User-agent: OAI-SearchBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: anthropic-ai
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: PerplexityBot
Allow: /

# Curated summary for LLM clients: /llms.txt
"""
    path.write_text(content, encoding="utf-8")


def write_llms_txt(path: Path, page_url: str, incubator_dir: Path) -> None:
    versions = versions_from_index(incubator_dir / "index.yaml")
    latest = sorted({v["version"] for v in versions}, key=semver_key, reverse=True)[:15]
    version_block = "\n".join(f"- {v}" for v in latest) or "- (no packages yet)"

    content = f"""# helm-charts (Foundry VTT Helm repository)

> {page_url}

Helm repository publishing the `foundry-vtt` chart for Kubernetes. Chart sources live in {SOURCE_REPO} under `incubator/foundry-vtt`. Each GitHub Pages release corresponds to a `foundry-*` tag on a `foundryvtt/*` track branch in that repository.

## Install

- Repository: {page_url}/
- Add repo: `helm repo add foundry-vtt {page_url}/`
- Chart: `{CHART_NAME}`
- Index: {page_url}/index.yaml

## Latest chart versions

{version_block}

## Key files

- [README.md]({page_url.replace("/incubator", "")}/README.md): human-oriented overview and release commands
- [incubator/index.yaml]({page_url}/index.yaml): Helm repository index
- [incubator/artifacthub-repo.yml]({page_url}/artifacthub-repo.yml): Artifact Hub metadata

## Source

- Charts: {SOURCE_REPO}
- Felddy container: https://github.com/felddy/foundryvtt-docker
"""
    path.write_text(content, encoding="utf-8")


def cmd_package(args: argparse.Namespace) -> None:
    charts_repo = Path(args.charts_repo).resolve()
    helm_root = Path(args.helm_charts).expanduser().resolve()
    incubator = helm_root / "incubator"
    if not incubator.is_dir():
        raise SystemExit(
            f"helm-charts incubator not found at {incubator}\n"
            f"  Pass the GitHub Pages repo root: --helm-charts /path/to/helm-charts\n"
            f"  Defaults: charts={DEFAULT_CHARTS_REPO}, helm-charts={DEFAULT_HELM_CHARTS}"
        )
    print(f"source: {charts_repo}", file=sys.stderr)
    print(f"output: {incubator}/", file=sys.stderr)
    ensure_charts_repo(charts_repo, args.charts_remote)

    tags: list[str] = []
    if args.all:
        tags = list_release_tags(charts_repo)
        if not args.force:
            print("note: --all re-packages every tag (same as --all --force)", file=sys.stderr)
        args.force = True
    elif args.all_new:
        for tag in list_release_tags(charts_repo):
            ver = chart_version_at_ref(charts_repo, tag)
            if args.modern_only and not is_modern_chart_version(ver):
                continue
            tgz = incubator / f"{CHART_NAME}-{ver}.tgz"
            if not tgz.is_file():
                tags.append(tag)
    else:
        tags = list(args.tag or [])
    if not tags:
        print("No tags to package.", file=sys.stderr)
        return

    print(f"packaging {len(tags)} tag(s)", file=sys.stderr)
    for tag in tags:
        try:
            run(["git", "rev-parse", "--verify", f"{tag}^{{commit}}"], cwd=charts_repo)
            ver = chart_version_at_ref(charts_repo, tag)
            tgz = incubator / f"{CHART_NAME}-{ver}.tgz"
            if tgz.is_file() and not args.force:
                print(f"skip {tag}: {tgz.name} already exists (use --force)", file=sys.stderr)
                continue
            with tempfile.TemporaryDirectory(prefix="foundry-vtt-pack-") as tmp:
                chart_dir = Path(tmp) / CHART_NAME
                export_chart_tree(charts_repo, tag, chart_dir)
                out = package_chart(chart_dir, incubator)
                fv = foundry_version_label(charts_repo, tag)
                print(f"packaged {tag} -> {out.name} (foundry {fv}, chart {ver})")
        except subprocess.CalledProcessError as exc:
            print(f"failed {tag}: {exc.stderr or exc}", file=sys.stderr)
            continue
        except SystemExit as exc:
            print(f"failed {tag}: {exc}", file=sys.stderr)
            continue


def cmd_reindex(args: argparse.Namespace) -> None:
    incubator = Path(args.helm_charts).resolve() / "incubator"
    full = getattr(args, "full_index", False) or getattr(args, "full", False)
    reindex(incubator, args.page_url, merge=not full)


def resolve_tags_for_package(args: argparse.Namespace, charts_repo: Path) -> None:
    """Used by release to validate tag selection before work."""
    if not args.tag and not args.all_new and not args.all:
        raise SystemExit("package requires --tag, --all-new, or --all")


def cmd_update_docs(args: argparse.Namespace) -> None:
    helm_root = Path(args.helm_charts).resolve()
    incubator = helm_root / "incubator"
    write_readme(helm_root, incubator, args.page_url)
    write_robots_txt(helm_root / "robots.txt")
    write_robots_txt(incubator / "robots.txt")
    write_llms_txt(helm_root / "llms.txt", args.page_url, incubator)
    write_llms_txt(incubator / "llms.txt", args.page_url, incubator)


def cmd_release(args: argparse.Namespace) -> None:
    resolve_tags_for_package(args, Path(args.charts_repo))
    cmd_package(args)
    if args.all:
        args.full_index = True
    cmd_reindex(args)
    cmd_update_docs(args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--charts-repo",
        type=Path,
        default=DEFAULT_CHARTS_REPO,
        help=f"Source git repo with foundry-* tags (default: {DEFAULT_CHARTS_REPO})",
    )
    parser.add_argument("--charts-remote", default="https://github.com/hugoprudente/charts.git")
    parser.add_argument(
        "--helm-charts",
        type=Path,
        default=DEFAULT_HELM_CHARTS,
        help=f"GitHub Pages repo root containing incubator/ (default: {DEFAULT_HELM_CHARTS})",
    )
    parser.add_argument("--page-url", default=REPO_PAGE_URL)
    parser.add_argument("--force", action="store_true", help="re-package even if .tgz exists")
    parser.add_argument(
        "--modern-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="with --all-new, skip ancient chart versions (0.2.x, 0.3, 8.9.0); use --no-modern-only for every tag",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_pack = sub.add_parser("package", help="export chart at tag(s) and helm package")
    p_pack.add_argument("--tag", action="append", help="charts repo tag (e.g. foundry-14.368)")
    p_pack.add_argument(
        "--all",
        action="store_true",
        help="package every release tag (foundry-*, foundryvtt-*, foundry0.x); overwrites .tgz",
    )
    p_pack.add_argument("--all-new", action="store_true", help="package tags missing from incubator/")
    p_pack.set_defaults(func=cmd_package, modern_only=True, full_index=False)

    p_idx = sub.add_parser("reindex", help="helm repo index")
    p_idx.add_argument(
        "--full",
        action="store_true",
        help="rebuild index from all .tgz in incubator/ (no --merge)",
    )
    p_idx.set_defaults(func=cmd_reindex, full_index=False)

    p_doc = sub.add_parser("update-docs", help="refresh README, robots.txt, llms.txt")
    p_doc.set_defaults(func=cmd_update_docs)

    p_rel = sub.add_parser("release", help="package + reindex + update-docs")
    p_rel.add_argument("--tag", action="append")
    p_rel.add_argument("--all", action="store_true", help="package all release tags + full reindex")
    p_rel.add_argument("--all-new", action="store_true")
    p_rel.set_defaults(func=cmd_release, modern_only=True, full_index=False)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
