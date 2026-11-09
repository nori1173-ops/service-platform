# 公開版の検証記録

2026-11-09 時点の公開ツリーでローカルに実施した検証の記録です。

| 検証 | 結果 |
| --- | --- |
| `uv run pytest` | 1,558 件成功 |
| `npm ci` | 完了 |
| `npm run lint` | 成功 |
| `npm run build` | TypeScript の型チェックと Vite ビルド成功 |
| `npx vitest run` | 47 ファイル・776 件成功 |
| CloudFront Function の Node テスト | 10 件成功 |
| `ruff check` / `ruff format --check` | 成功 |
| `cfn-lint` | 8 テンプレート成功。7 実体と、sample-app として展開した雛形 |
| 匿名化・公開範囲の検査 | 実在識別子・私設 IP・非公開アプリの混入なし |
| 改行・BOM・文字コード | 変更した既存ファイルは公開版、新規取り込みはソースと一致。UTF-8、BOM なし |

`npm run build` の `vue-tsc -b` は `frontend/e2e/**/*.ts` と Playwright 設定も対象とします。
AWS を呼ぶ E2E と PoC は、このローカル検証では実行していません。README と ADR に記した実測は、元の開発時の検証記録です。

構成図の個別サービスは配置例です。カメラアプリの実装とスクリーンショットは収録していません。
