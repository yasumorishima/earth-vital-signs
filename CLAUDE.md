# CLAUDE.md

NOAA / NSIDC / Copernicus の気候指標を毎日 CSV にして `data/` にコミットし、Kaggle データセット `yasunorim/earth-vital-signs-daily` に公開するリポジトリ。

## 構成
- `scripts/build.py`: 取得 → 派生列 → ゲート。どれか1つでもゲートが FAIL なら `data/` に書かずに終了 1。`data/` と Kaggle 用の `_build/` を書く。
- `scripts/make_kaggle_meta.py`: `kaggle/dataset-metadata.json` と `kaggle/settings.json` を生成し、CSV の列と食い違えば失敗。
- `scripts/kaggle_published.py`: Kaggle 最新版の版メモ `(commit <sha>)` とリポジトリを比べ、`behind=true|false` を出す。
- テスト(ネット不要、CI でも実行): `python scripts/test_get.py`、`python scripts/test_derived.py`。
- `scripts/make_*notebook.py` はノートブック生成用(一度だけ実行、成果物はコミット済み)。`mutants_derived.py` はゲートの効き目検査(要ネット)。

## ワークフロー `.github/workflows/update.yml`
- 定期実行は 06:17 / 13:47 / 21:23 UTC の1日3回。GitHub 側で数時間遅れることがよくある。
- 公開するのは、`inputs.publish` が true か、定期実行で `vars.KAGGLE_PUBLISH == 'true'` かつ `behind=true` のとき。公開に失敗しても次の定期実行が自動でやり直す。
- Kaggle の版メモは `Data through <sst last_date> (commit <data/ の最終コミット>)`。この書式を変えると `kaggle_published.py` の判定が壊れる。
- checkout の `fetch-depth: 50` は、`git log -- data/` が最終データコミットを見つけるために必要。
- `build.py` が終了 75 のときは、ソースに届かなかったことを表す(通信エラー、5xx/408/429、ダウンロード停滞)。定期実行で `data/` の更新から 48 時間未満なら、警告だけ出して成功扱いにし、コミットと Kaggle を飛ばす。手動実行や 48 時間以上経っている場合は失敗にする。
- 通信エラーのやり直しは 30 秒・1・2・4・5 分(計約 12 分)。HTTP 429/5xx は従来どおり。

## ハマりどころ
- kaggle CLI は 2.2.4 に固定。新しい版が出ると「Warning: outdated kaggle version」を stdout の JSON より前に出すので、`kaggle ... --format json` の出力は必ず `grep '^{'` してから jq に渡す。`import kaggle` は import 時に認証する。
- Kaggle の版一覧は `curl -sS https://www.kaggle.com/api/v1/datasets/view/yasunorim/earth-vital-signs-daily` で確認できる(Claude Code のクラウド環境ではプロキシが認証を付ける)。
- Claude の GitHub 連携には Actions の権限がない。そのためワークフローの手動実行(workflow_dispatch)はできず 403 になる。ログの閲覧、PR の作成・マージはできる。
- 作業ブランチの force push は自動モードで拒否される。マージ済みのブランチを再利用するときは、main から作り直して旧リモートブランチを通常の merge で取り込む(衝突は自分の側を採用)。

## 作業ルール
- 返答は日本語。PR を作ってマージまでしてよい(squash)。
- データを守るゲートや「失敗したら何も書かない」性質は弱めない。

## 経緯(2026-10)
- 10/22〜27: kaggle 2.2.5 が出て警告行で jq が壊れ、公開が止まった(Kaggle は v42 のまま)。PR #3 で直した。
- PR #4: 版メモにコミットを入れ、定期実行が公開漏れに自動で追いつくようにした。10/28 の run #88 で v43 を公開し、ノートブック2本の再実行も成功した。
- PR #5: 通信断(run #87、NOAA GML に Errno 101)で失敗メールが来ないようにした。
- 未確認: データが変わらない定期実行で `behind=false` になり、二重に公開しないこと(10/29 01:00 UTC に確認予約あり)。
