"""Tell whether the latest Kaggle version carries the data committed here.

Each upload's version notes end with "(commit <sha>)", the last commit that changed the
published files. A scheduled run compares that with the repository and publishes again
when they differ, so a failed upload is retried by the next run instead of waiting for
new data.

Usage: python scripts/kaggle_published.py <owner>/<dataset> <commit>
Writes behind=true|false to $GITHUB_OUTPUT (if set) and prints it.
"""
from __future__ import annotations

import os
import re
import sys

NOTES_COMMIT = re.compile(r"\(commit ([0-9a-f]{7,40})\)")


def notes_commit(notes: str | None) -> str | None:
    m = NOTES_COMMIT.search(notes or "")
    return m.group(1) if m else None


def is_behind(published: str | None, commit: str) -> bool:
    # short and full hashes of the same commit share a prefix; no marker = an older upload
    return not published or not (commit.startswith(published) or published.startswith(commit))


def latest_notes(dataset: str) -> tuple[int, str]:
    from kaggle.api.kaggle_api_extended import KaggleApi
    from kagglesdk.datasets.types.dataset_api_service import ApiGetDatasetRequest

    api = KaggleApi()
    api.authenticate()
    owner, slug = dataset.split("/")
    req = ApiGetDatasetRequest()
    req.owner_slug = owner
    req.dataset_slug = slug
    with api.build_kaggle_client() as kaggle:
        ds = kaggle.datasets.dataset_api_client.get_dataset(req)
    versions = [v for v in (ds.versions or []) if v]
    if not versions:
        raise SystemExit(f"{dataset}: no versions returned")
    v = max(versions, key=lambda v: v.version_number)
    return v.version_number, v.version_notes or ""


def main() -> None:
    dataset, commit = sys.argv[1], sys.argv[2]
    number, notes = latest_notes(dataset)
    published = notes_commit(notes)
    behind = is_behind(published, commit)
    print(f"Kaggle version {number}: {notes!r}; repository data commit {commit}")
    print(f"behind={'true' if behind else 'false'}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"behind={'true' if behind else 'false'}\n")


if __name__ == "__main__":
    main()
