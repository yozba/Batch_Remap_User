# Batch Remap User

Blender 5.x向けの拡張。Outlinerで選択した複数のデータブロックを、外部Blendファイル内の同名・同種のデータブロックへ一括Remapします。**View Layer / Scenes / Blender File / Orphan Data** などの表示モードに対応し、移行先がまだLinkされていなくても使えます。

## インストール

### Local Repositoryから直接読み込む

Preferences → Get Extensions → Repositories → **＋ → Add Local Repository** で、`C:\Users\sankaku\Documents\gitrepository\` をDirectoryに指定してください。

```text
C:\Users\sankaku\Documents\gitrepository\
└─ Batch_Remap_User
   ├─ blender_manifest.toml
   └─ __init__.py
```

リポジトリに指定するのは `gitrepository` です。その直下の `Batch_Remap_User` がひとつの拡張になります。実装はその中の `batch_remap_user` にあります。追加後にローカルの一覧を更新し、Add-onsで **Batch Remap User** を有効にしてください。[Blender公式のLocal Repository説明](https://docs.blender.org/manual/sl/5.0/editors/preferences/extensions.html)

### ZIPからインストール

このフォルダーで `python tools/build.py` を実行すると、インストール用ZIPを `dist/` に生成します。

Preferences → Add-ons → 右上のメニュー → **Install from Disk** から `dist/batch_remap_user-0.3.0.zip` を選択し、有効にしてください。ZIPは展開せずに指定します。

Local Repositoryでソースを更新した場合は、Blenderを再起動して変更を読み直してください。

## 使い方

1. Outlinerで、置き換えたいデータブロックを複数選択します。表示モードをBlender Fileに変える必要はありません。異なる種類を混在させても構いません。
2. 右クリック → **Batch Remap User**。データの種類によっては **ID Data** 内にも表示されます。Outliner上でF3から検索しても実行できます。
3. ポップアップの **Target Blend File** で移行先を選びます。既にLink済みのBlendファイルはそのまま選択してOKを押せます。同じファイル名でもフルパスで区別できます。
4. 別のファイルを指定する場合は **Choose Blend File...** を選びます。同じポップアップに表示される **Blend File Path** に移行先の `.blend` のパスを入力してOKを押すと、必要な同名データを初めてLinkしてからRemapします。**Browse...** ボタンからファイルブラウザーで選ぶこともできます。

**Link Missing Matching Data** はリンク済みファイルを選んだ場合の設定です。ONの場合、移行先ファイルに存在するが現在のBlendに未Linkの同名データも追加でLinkします。OFFの場合は読み込み済みのデータだけで照合します。ファイルブラウザーから指定した場合は常に同名データをLinkします。追加Linkに伴ってBlenderが依存データも読み込むことがあります。

事前のLinkは不要です。ファイルブラウザーをキャンセルした場合や、同名データが一件もない場合は、新しいLinkを作りません。ファイルブラウザーを開く前の選択内容を保持して処理します。

例えば、ローカルのMaterial `Wood` / `Metal` を選択し `assets.blend` を指定すると、両方を同ファイルのMaterial `Wood` / `Metal` に置き換えます。他のBlendからLinkしたデータも移行元にできます。

既にLinkされているCollection `build_004` を選び、新しいBlendファイルのパスを入力すれば、そのファイルの同名Collectionに参照を移せます。移行元がMissing状態でも処理できます。移行先のデータは正常に読み込めるものを使用します。

## Find Missing Linked Libraries

データブロックを選択せずに、欠損したLinked Libraryを指定のBlendファイルへまとめて付け替えます。

1. **File → External Data → Find Missing Linked Libraries...** を実行します。F3で名前を検索しても実行できます。
2. ファイルブラウザーで、復旧先に使いたい `.blend` をひとつ指定します。フォルダー検索は行いません。
3. 指定ファイル内の同名・同種のデータで復旧し、ライブラリを再読み込みします。元と違うBlendファイル名でも指定できます。

- 対象は、現在のファイルにあるリンク先Blendが欠損しているLibrary全体です。正常なLibraryは対象にしません。
- 複数の欠損Libraryが同名・同種のデータを参照している場合は、指定Blend内の同じデータへの参照になります。
- 指定ファイルに見つからないデータは、元の欠損リンクを残し、未解決として件数と詳細を報告します。
- 別のBlend経由で読み込まれた間接Libraryは、Blenderが直接のRelocateを禁止しています。未解決として修正が必要な親Blendのパスをシステムコンソールに表示します。親Blendを開いてこのオペレーター等で修正してください。
- 画像などの外部ファイルのパスや、外部Blendファイルそのものは書き換えません。Ctrl+Zで戻せます。

Blender 5.1標準の **Find Missing Files** でも欠損ライブラリのパスは検索できますが、復旧後に再読み込みが必要な場合があります。この追加オペレーターは、明示的に指定したBlendファイルを使い、ライブラリの付け替えと再読み込みをまとめて行います。[Blender 5.1マニュアル](https://docs.blender.org/manual/id/5.1/interface/window_system/topbar.html)

## 照合と処理範囲

- 名前は大文字小文字を含めて完全一致。`Wood.001` は `Wood` と一致しません。
- MaterialはMaterial、MeshはMeshなど、同じデータ型だけが対象です。Node Groupも具体的なツリー型まで照合します。
- 選択したデータブロックそのものが対象です。Objectを選んでも、そのMeshやMaterialを個別にRemapする処理は追加しません。
- Remapは選択したIDの参照を置き換えます。同じIDを共有している未選択のObjectなどにも反映されます。[Blenderの `ID.user_remap()`](https://docs.blender.org/api/5.0/bpy.types.ID.html) を使用します。
- 移行元データブロックは削除しません。不要になったデータの整理はBlender標準のPurgeなどで行えます。
- 同名データが見つからないもの、移行先自身、Library見出し、Library Override、埋め込みデータはスキップします。Edit Mode等のObjectもスキップします。Missing状態のリンクデータも移行元にできますが、Missing状態のデータは移行先に使用しません。
- Libraryや種類の見出しを選ぶだけで、その配下を再帰的に処理する機能はありません。対象データの行を選択してください。
- 外部Blendファイルは書き換えません。リンク元の内部参照・Overrideの再構築にはBlender自身のRemapの制約が適用されます。
- 移行先には外部の `.blend` を指定してください。現在開いているBlendファイル自身は指定できません。
- Collectionの移行元と移行先が同じローカルの親にリンクされている場合は、移行先のリンクを残して重複を解消します。リンク元やOverrideの親に重複がある場合、または移行先の子孫に移行元がある場合は、理由を表示して処理を失敗扱いにします。
- 実行結果は件数を表示し、スキップ・失敗の詳細はシステムコンソールに出力します。Ctrl+Zで戻せます。
- Undoで参照は元に戻りますが、Blenderの仕様により追加Linkしたデータが未使用の状態で残ることがあります。不要ならPurgeで整理してください。

## 開発・検証

`python tools/build.py` で配布ZIPを作成します。外部依存パッケージはありません。

Blender本体での統合テスト:

```powershell
& 'path\to\blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_integration.py
& 'path\to\blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_missing_libraries.py
```

テストは一時フォルダー内でBlendファイルを作成し、初回Link、ポップアップでの移行先パス指定、Missing状態のCollection・Materialの復旧、ファイルブラウザーでの選択保持、複数のOutliner表示モード、同名・異種・複数ライブラリ・未Linkデータ・選択外データ・Undoを確認します。利用中のファイルには触れません。

ライブラリ復旧のテストでは、選択なしでの複数Libraryの復旧、部分一致時の未解決リンク保持、正常な同名Libraryデータ・画像パスの維持、相対パス、間接リンクの報告、Undoを確認します。
