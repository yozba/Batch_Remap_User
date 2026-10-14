# Batch Remap User

Blender 5.x向けの拡張。Outlinerの **Blender File** で選択した複数のデータブロックを、既にLinkしている外部Blendファイル内の同名・同種のデータブロックへ一括Remapします。

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

Preferences → Add-ons → 右上のメニュー → **Install from Disk** から `dist/batch_remap_user-0.1.1.zip` を選択し、有効にしてください。ZIPは展開せずに指定します。

Local Repositoryでソースを更新した場合は、Blenderを再起動して変更を読み直してください。

## 使い方

1. 移行先の外部Blendファイルから、何かひとつ以上のデータブロックを **Link** しておきます。
2. Outlinerの表示モードを **Blender File** に切り替えます。
3. 置き換えたいデータブロックを複数選択します。異なる種類を混在させても構いません。
4. 右クリック → **Batch Remap User**。データの種類によっては **ID Data** 内にも表示されます。Outliner上でF3から検索しても実行できます。
5. ポップアップで **Target Blend File** を選び、OKを押します。同じファイル名でもフルパスで区別できます。

**Link Missing Matching Data** がONの場合、移行先ファイルに存在するが現在のBlendに未Linkの同名データも追加でLinkします。OFFの場合は読み込み済みのデータだけで照合します。追加Linkに伴ってBlenderが依存データも読み込むことがあります。

例えば、ローカルのMaterial `Wood` / `Metal` を選択し `assets.blend` を指定すると、両方を同ファイルのMaterial `Wood` / `Metal` に置き換えます。他のBlendからLinkしたデータも移行元にできます。

## 照合と処理範囲

- 名前は大文字小文字を含めて完全一致。`Wood.001` は `Wood` と一致しません。
- MaterialはMaterial、MeshはMeshなど、同じデータ型だけが対象です。Node Groupも具体的なツリー型まで照合します。
- 選択したデータブロックそのものが対象です。Objectを選んでも、そのMeshやMaterialを個別にRemapする処理は追加しません。
- Remapは選択したIDの参照を置き換えます。同じIDを共有している未選択のObjectなどにも反映されます。[Blenderの `ID.user_remap()`](https://docs.blender.org/api/5.0/bpy.types.ID.html) を使用します。
- 移行元データブロックは削除しません。不要になったデータの整理はBlender標準のPurgeなどで行えます。
- 同名データが見つからないもの、移行先自身、Library見出し、Library Override、埋め込みデータ、欠損したリンクデータはスキップします。Edit Mode等のObjectもスキップします。
- Libraryや種類の見出しを選ぶだけで、その配下を再帰的に処理する機能はありません。対象データの行を選択してください。
- 外部Blendファイルは書き換えません。リンク元の内部参照・Overrideの再構築にはBlender自身のRemapの制約が適用されます。
- Collectionの移行元と移行先が同じローカルの親にリンクされている場合は、移行先のリンクを残して重複を解消します。リンク元やOverrideの親に重複がある場合、または移行先の子孫に移行元がある場合は、理由を表示して処理を失敗扱いにします。
- 実行結果は件数を表示し、スキップ・失敗の詳細はシステムコンソールに出力します。Ctrl+Zで戻せます。

## 開発・検証

`python tools/build.py` で配布ZIPを作成します。外部依存パッケージはありません。

Blender本体での統合テスト:

```powershell
& 'path\to\blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_integration.py
```

テストは一時フォルダー内でBlendファイルを作成し、同名・異種・複数ライブラリ・未Linkデータ・選択外データ・Undoを確認します。利用中のファイルには触れません。
