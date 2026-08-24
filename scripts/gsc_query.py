#!/usr/bin/env python3
"""Google Search Console の Search Analytics API を叩く汎用クエリツール。

Bashツールから呼び出すことを想定し、結果は標準出力へJSON形式で1回だけ出力する。
認証や集計方法はtakisys本体の App\\Services\\SearchConsoleService.php を踏襲している。
"""

import argparse
import json
import os
import sys
from typing import Any

# Search Console APIが受け付ける有効なディメンション一覧
VALID_DIMENSIONS = {"query", "page", "country", "device", "date"}

# rowLimitの上限（Search Analytics APIの仕様上の最大値）
MAX_ROW_LIMIT = 25000

# 認証情報パスが未設定の場合に案内する環境変数名
CREDENTIALS_ENV_NAME = "GOOGLE_SERVICE_ACCOUNT_JSON"


def eprint(message: str) -> None:
    """エラーメッセージを標準エラー出力へ書き出す。引数: message(表示する文字列)。戻り値: なし。"""
    print(message, file=sys.stderr)


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を定義・解析する。引数: なし。戻り値: 解析済みのargparse.Namespace。"""
    parser = argparse.ArgumentParser(
        description="Google Search Console の検索パフォーマンスデータを取得してJSONで出力する"
    )
    site_group = parser.add_mutually_exclusive_group()
    site_group.add_argument(
        "--site",
        help="config/sites.json に登録済みのサイトキー（例: example-shop）",
    )
    site_group.add_argument(
        "--site-url",
        help="Search Consoleのプロパティ URL を直接指定する（例: https://example.com/）",
    )
    parser.add_argument("--start-date", required=True, help="取得開始日（YYYY-MM-DD）")
    parser.add_argument("--end-date", required=True, help="取得終了日（YYYY-MM-DD）")
    parser.add_argument(
        "--dimensions",
        required=True,
        help="カンマ区切りのディメンション。query/page/country/device/date から選択",
    )
    parser.add_argument(
        "--row-limit",
        type=int,
        default=100,
        help="取得行数の上限（デフォルト100、最大25000）",
    )
    parser.add_argument(
        "--search-type",
        default="web",
        help="検索タイプ（デフォルト web。他に image/video/news 等）",
    )
    parser.add_argument("--compare-start-date", help="比較期間の開始日（YYYY-MM-DD）")
    parser.add_argument("--compare-end-date", help="比較期間の終了日（YYYY-MM-DD）")
    parser.add_argument(
        "--credentials",
        help="サービスアカウントJSON鍵の絶対パス（省略時は環境変数 GOOGLE_SERVICE_ACCOUNT_JSON を使用）",
    )
    parser.add_argument(
        "--properties-file",
        "--sites-file",
        dest="properties_file",
        help="サイト定義JSONのパス（省略時はスクリプトと同階層の ../config/sites.json）",
    )
    return parser.parse_args()


def resolve_dimensions(raw: str) -> list[str]:
    """カンマ区切りのディメンション文字列を検証しリスト化する。引数: raw(カンマ区切り文字列)。戻り値: ディメンション名のリスト。"""
    dimensions = [d.strip() for d in raw.split(",") if d.strip()]
    if not dimensions:
        eprint("エラー: --dimensions が空です。query/page/country/device/date から1つ以上指定してください。")
        sys.exit(1)

    invalid = [d for d in dimensions if d not in VALID_DIMENSIONS]
    if invalid:
        eprint(
            "エラー: 無効なディメンションが指定されました: "
            + ", ".join(invalid)
            + f"\n有効な値: {', '.join(sorted(VALID_DIMENSIONS))}"
        )
        sys.exit(1)

    return dimensions


def resolve_row_limit(row_limit: int) -> int:
    """rowLimitが有効範囲内か検証する。引数: row_limit(指定された取得行数)。戻り値: 検証済みの取得行数。"""
    if row_limit < 1 or row_limit > MAX_ROW_LIMIT:
        eprint(f"エラー: --row-limit は1〜{MAX_ROW_LIMIT} の範囲で指定してください（指定値: {row_limit}）。")
        sys.exit(1)
    return row_limit


def load_env_file(env_path: str) -> dict[str, str]:
    """KEY=VALUE形式の.envファイルを簡易パースする。引数: env_path(.envファイルパス)。戻り値: キーと値のdict。"""
    values: dict[str, str] = {}
    if not os.path.isfile(env_path):
        return values

    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")

    return values


def resolve_credentials_path(args_credentials: str | None) -> str:
    """認証情報ファイルのパスを解決し存在確認する。優先順位は --credentials > 環境変数 > スキル直下の.envファイル。引数: args_credentials(--credentials指定値、なければNone)。戻り値: 検証済みの絶対パス。"""
    path = args_credentials or os.environ.get(CREDENTIALS_ENV_NAME)

    if not path:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        skill_root = os.path.dirname(script_dir)
        env_file = os.path.join(skill_root, ".env")
        path = load_env_file(env_file).get(CREDENTIALS_ENV_NAME)

    if not path:
        eprint(
            "エラー: サービスアカウントJSON鍵のパスが指定されていません。\n"
            f"スキル直下の .env ファイルに {CREDENTIALS_ENV_NAME}=/path/to/service-account.json を設定するか、"
            f"環境変数 {CREDENTIALS_ENV_NAME}、または --credentials で明示的に指定してください。"
        )
        sys.exit(1)

    if not os.path.isfile(path):
        eprint(f"エラー: 指定されたサービスアカウントJSON鍵が見つかりません: {path}")
        sys.exit(1)

    return path


def resolve_properties_file(args_properties_file: str | None) -> str:
    """サイト定義JSONファイルのパスを解決する。引数: args_properties_file(--properties-file指定値、なければNone)。戻り値: 解決済みパス文字列。"""
    if args_properties_file:
        return args_properties_file

    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, "..", "config", "sites.json")


def resolve_property_url(args: argparse.Namespace) -> str:
    """--site または --site-url からSearch ConsoleのプロパティURLを解決する。引数: args(解析済みコマンドライン引数)。戻り値: プロパティURL文字列。"""
    if args.site_url:
        return args.site_url

    if not args.site:
        eprint("エラー: --site または --site-url のいずれかを指定してください。")
        sys.exit(1)

    properties_file = resolve_properties_file(args.properties_file)
    if not os.path.isfile(properties_file):
        eprint(f"エラー: サイト定義ファイルが見つかりません: {properties_file}")
        sys.exit(1)

    try:
        with open(properties_file, encoding="utf-8") as f:
            sites = json.load(f)
    except json.JSONDecodeError as e:
        eprint(f"エラー: サイト定義ファイルのJSONが不正です（{properties_file}）: {e}")
        sys.exit(1)

    site_config = sites.get(args.site)
    if not site_config or not site_config.get("property_url"):
        available = ", ".join(sites.keys()) if sites else "（登録なし）"
        eprint(
            f"エラー: サイトキー '{args.site}' が {properties_file} に見つかりません。\n"
            f"登録済みのサイトキー: {available}"
        )
        sys.exit(1)

    return site_config["property_url"]


def build_service(credentials_path: str) -> Any:
    """サービスアカウント鍵からSearch Console APIクライアントを構築する。引数: credentials_path(JSON鍵の絶対パス)。戻り値: googleapiclientのSearch Consoleサービスオブジェクト。"""
    # ここでインポートすることで、引数エラー時にライブラリ未インストールでも早期にエラーメッセージを出せる
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
    )
    return build("searchconsole", "v1", credentials=credentials, cache_discovery=False)


def run_query(
    service: Any,
    property_url: str,
    start_date: str,
    end_date: str,
    dimensions: list[str],
    row_limit: int,
    search_type: str,
) -> list[dict[str, Any]]:
    """指定条件でSearch Analytics APIを実行し行データを取得する。引数: service(APIクライアント), property_url(対象プロパティURL), start_date/end_date(取得期間), dimensions(ディメンション名リスト), row_limit(取得行数上限), search_type(検索タイプ)。戻り値: dimensions名とclicks/impressions/ctr/positionをキーに持つ辞書のリスト。"""
    request_body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "searchType": search_type,
        "startRow": 0,
    }

    response = service.searchanalytics().query(siteUrl=property_url, body=request_body).execute()

    rows = []
    for row in response.get("rows", []):
        keys = row.get("keys", [])
        entry: dict[str, Any] = {}
        for dimension_name, key_value in zip(dimensions, keys):
            entry[dimension_name] = key_value
        entry["clicks"] = row.get("clicks", 0)
        entry["impressions"] = row.get("impressions", 0)
        entry["ctr"] = row.get("ctr", 0.0)
        entry["position"] = row.get("position", 0.0)
        rows.append(entry)

    return rows


def main() -> None:
    """コマンドライン引数を解析し、Search Consoleデータを取得してJSONを標準出力へ出力する。引数: なし。戻り値: なし（正常終了時はexit code 0、エラー時は1）。"""
    args = parse_args()

    dimensions = resolve_dimensions(args.dimensions)
    row_limit = resolve_row_limit(args.row_limit)
    credentials_path = resolve_credentials_path(args.credentials)
    property_url = resolve_property_url(args)

    try:
        service = build_service(credentials_path)
    except ImportError as e:
        eprint(
            "エラー: 必要なPythonパッケージがインストールされていません。\n"
            "スキルディレクトリで `pip3 install -r scripts/requirements.txt` を実行してください。\n"
            f"詳細: {e}"
        )
        sys.exit(1)
    except Exception as e:
        eprint(f"エラー: 認証情報の読み込みに失敗しました: {e}")
        sys.exit(1)

    try:
        rows = run_query(
            service,
            property_url,
            args.start_date,
            args.end_date,
            dimensions,
            row_limit,
            args.search_type,
        )
    except Exception as e:
        eprint(f"エラー: Search Console APIの呼び出しに失敗しました: {e}")
        sys.exit(1)

    result: dict[str, Any] = {
        "site": args.site or property_url,
        "property_url": property_url,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "dimensions": dimensions,
        "search_type": args.search_type,
        "rows": rows,
    }

    if args.compare_start_date and args.compare_end_date:
        try:
            compare_rows = run_query(
                service,
                property_url,
                args.compare_start_date,
                args.compare_end_date,
                dimensions,
                row_limit,
                args.search_type,
            )
        except Exception as e:
            eprint(f"エラー: 比較期間のSearch Console API呼び出しに失敗しました: {e}")
            sys.exit(1)

        result["compare"] = {
            "start_date": args.compare_start_date,
            "end_date": args.compare_end_date,
            "rows": compare_rows,
        }
    elif args.compare_start_date or args.compare_end_date:
        eprint(
            "エラー: 比較期間を取得するには --compare-start-date と --compare-end-date の両方を指定してください。"
        )
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
