# CLAUDE.md

NOAA / NSIDC / Copernicus の気候指標を毎日 CSV にして `data/` にコミットし、Kaggle データセット `yasunorim/earth-vital-signs-daily` に公開するリポジトリ。

## 構成
- `scripts/build.py`: 取得 → 派生列 → ゲート。どれか1つでもゲートが FAIL なら `data/` に書かずに終了 1。`data/` と Kaggle 用の `_build/` を書く。
- `scripts/make_kaggle_meta.py`: `kaggle/dataset-metadata.json` と `kaggle/settings.json` を生成し、CSV の列と食い違えば失敗。
- `scripts/kaggle_published.py`: Kaggle 最新版の版メモ `(commit <sha>)` とリポジトリを比べ、`behind=true|false` を出す。
- `scripts/make_charts.py`: README の図(race.gif、気温・海氷・海面水温・CO2 の PNG)を描く。ワークフローの最後のステップが `charts` ブランチに1コミットで force push し、README は raw.githubusercontent.com の `charts/` を参照する(main の履歴に画像をためないため)。
- テスト(ネット不要、CI でも実行): `python scripts/test_get.py`、`python scripts/test_derived.py`。
- `scripts/make_*notebook.py` はノートブック生成用(一度だけ実行、成果物はコミット済み)。`mutants_derived.py` はゲートの効き目検査(要ネット)。

## ワークフロー `.github/workflows/update.yml`
- 定期実行は 06:17 / 13:47 / 21:23 UTC の1日3回。GitHub 側で数時間遅れることがよくある。
- 公開するのは、`inputs.publish` が true か、定期実行で `vars.KAGGLE_PUBLISH == 'true'` かつ `behind=true` のとき。公開に失敗しても次の定期実行が自動でやり直す。
- Kaggle の版メモは `Data through <sst last_date> (commit <data/ の最終コミット>)`。この書式を変えると `kaggle_published.py` の判定が壊れる。
- checkout の `fetch-depth: 50` は、`git log -- data/` が最終データコミットを見つけるために必要。
- `build.py` が終了 75 のときは、ソースに届かなかったことを表す(通信エラー、5xx/408/429、ダウンロード停滞)。定期実行で `data/` の更新から 48 時間未満なら、警告だけ出して成功扱いにし、コミットと Kaggle を飛ばす。手動実行や 48 時間以上経っている場合は失敗にする。
- 通信エラーのやり直しは 30 秒・1・2・4・5 分(計約 12 分)。HTTP 429/5xx は従来どおり。
- ノートブックの再実行(`kaggle kernels push`)は1本につき最大3回、1分・2分空けてやり直す。

## ハマりどころ
- kaggle CLI は 2.2.4 に固定。新しい版が出ると「Warning: outdated kaggle version」を stdout の JSON より前に出すので、`kaggle ... --format json` の出力は必ず `grep '^{'` してから jq に渡す。`import kaggle` は import 時に認証する。
- kaggle 2.2.4 は `six` を import するのに依存に書いていない。python-dateutil 4.0.0 で `six` が入らなくなり、run #101 が `No module named 'six'` で失敗した。そのため `pip install kaggle==2.2.4 six` としている。
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
- 確認済み: データが変わらない定期実行(11/1 の run #100)は、`behind=false` で公開とノートブック再実行を飛ばした。
- 11/1 の run #101: `six` がなくて失敗した。依存に足して直した(上記)。
- 11/9 の run #125: データは v69 で公開できたが、ノートブックの送信が Kaggle 側の「The operation was canceled.」で失敗した。送信をやり直すようにした(上記)。データ公開済みなので次の実行は behind=false になり、ノートブックは次のデータ更新のときに再実行される。

## 確かめ方の作法(2026-11 追記)
Anthropic「Building strong safety evaluations」(2026-11-13)の教訓を、この repo に当てはめたもの。

- **主張の前に、何を測るかを書く。** ノートブックの「記録」は、同じ暦日の中での順位(比べた年数が `of`、10年未満なら記録にしない)。「1.5 度超え」は、C3S の日別オフセットで 1850-1900 基準に直した日値で、パリ協定の 20 年平均を超えたという意味ではない。「今年は最も暑いか」は、同じ日付までの年初来平均の順位。列や主張を足すときは、この定義を先にノートブックと列の説明に書く。
- **ゲートは、わざと入れた欠陥で本当に落ちることを確かめて残す(`mutants_derived.py`)。** いま確かめてあるのは、C3S のオフセットと年平均、ENSO の CPC 表との照合、強制力の AGGI 表との照合、TSI。NCEI の別写しとの 0.005℃ 照合と、CPC 週値との照合はまだ入っていない。ゲートを足すときや閾値を変えるときは、欠陥を1つ足して CAUGHT になるのを確かめてから入れる。
- **照合が成り立たないのに黙って PASS にしない。** 照合先が取れない、読めない、比べる件数が足りない、のどれでも FAIL(届かないだけなら終了 75)にする。何件比べたか(何週、何年)はゲートのメッセージに出す。
- **驚く値は、まず仕掛けを疑う。** 新記録、大きな跳ね、急な FAIL が出たら、取得、単位、日付のずれ、源の版の変化を先に調べる。現象として扱うのは、それで説明がつかないときだけ。
- **差が誤差の幅に入るときは、そう書く。** ERA5 の preliminary や OISST の直近約2週は、あとで値が変わる。年初来の順位差や記録の差がその改訂の幅や丸めに入るなら「確定ではない」と書く。結果は区分(海域、南北、指標)ごとに出し、記録にならなかった指標も並べる。
- **源や道具の版が変わったら記録し、版の違う値は比べない。** 例:oni.ascii.txt が ERSST v5 から v6 に変わり、DJF 1950 が -1.5 から -1.32 になった。これは `build.py` のゲートが検出する。変化は経緯に日付つきで書き、ゲートで気づけるようにする。PDO(ERSST v5)と ONI(v6)のように版の違う値は、同じ基準のものとして並べない。kaggle CLI などの道具の版も同じように扱う。
