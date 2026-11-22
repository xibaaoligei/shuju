#!/usr/bin/env python3
"""
构建 GitHub Pages 用的在线只读版。

用法：
  python tools/build_static.py --db --out docs            # 用本机 SQLite 最新批次
  python tools/build_static.py --api --out _site         # 用理杏仁接口（GitHub Actions 用）
  python tools/build_static.py --db --out docs --no-encrypt  # 本地开发

环境变量：
  SITE_PASSPHRASE     加密密码（必填，除非 --no-encrypt）
  LIXINGER_COOKIES    理杏仁 cookies JSON（--api 时必填）

生成：
  <out>/index.html
  <out>/static-app.js
  <out>/qrcode.js
  <out>/data.enc.json   （或 data.json）
  <out>/.nojekyll
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TEMPLATE = ROOT / "template"
SITE = ROOT / "site"

# 让 --db 模式能导入上一级目录的 db.py
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(TOOLS))

import scraper  # noqa: E402  (本地副本)

FIELD_CN = {
    "stockCode": "指数代码(API)", "indexCode": "指数代码(API)",
    "name": "名称", "indexName": "指数名称(API)",
    "market": "市场", "areaCode": "区域代码",
    "exchange": "交易所", "currency": "货币",
    "series": "系列", "source": "来源",
    "launchDate": "发布日期", "ipoDate": "上市日期",
    "calculationMethod": "计算方式", "fsTableType": "财报类型",
    "rebalancingFrequency": "调仓频率", "constituentCount": "成分股数量",
    "mutualMarkets": "互通市场", "mutualMarketFlag": "互通标记",
    "pe_ttm": "PE-TTM", "pb": "PB", "ps_ttm": "PS-TTM",
    "dyr": "股息率", "roe": "ROE", "mc": "总市值",
    "change_rate": "涨跌幅", "close_point": "收盘点位",
    "change_rate_ytd": "今年以来涨跌幅",
}


def cn(key: str) -> str:
    return FIELD_CN.get(key, key)


def flatten_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """把数据库/接口行扁平化成前端使用的列结构。"""
    all_keys = set()
    for row in rows:
        fields = row.get("fields")
        if isinstance(fields, dict):
            all_keys.update(fields.keys())

    columns = ["指数代码", "指数名称", "发布时间"]
    columns += sorted({cn(key) for key in all_keys}, key=lambda value: value)

    data = []
    for row in rows:
        flat = {
            "id": row.get("id"),
            "指数代码": row.get("index_code", ""),
            "指数名称": row.get("index_name", ""),
            "发布时间": row.get("data_date", ""),
        }
        fields = row.get("fields")
        if isinstance(fields, dict):
            for key, value in fields.items():
                flat[cn(key)] = value
        data.append(flat)
    return data, columns


def periods_payload(source: dict | None = None) -> dict:
    source = source or {}
    periods = [{"key": key, "label": label} for key, label in scraper.PERIODS]
    default_key = scraper.get_default_period(source)
    return {
        "periods": periods,
        "default": scraper.PERIOD_LABELS.get(default_key, ""),
    }


def make_payload(
    rows: list[dict],
    batch: dict,
    source: dict | None = None,
    favorites: list[dict] | None = None,
    preferences: dict | None = None,
) -> dict:
    data, columns = flatten_rows(rows)
    batch = {
        "id": batch.get("id", 1),
        "source_id": batch.get("source_id", 1),
        "batch_date": batch.get("batch_date") or date.today().isoformat(),
        "status": batch.get("status", "success"),
        "record_count": int(batch.get("record_count") or len(data)),
        "error_msg": batch.get("error_msg", ""),
        "created_at": batch.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    source_name = "指数数据（在线版）"
    return {
        "sources": [{
            "id": 1,
            "name": source_name,
            "url": (source or {}).get("url", ""),
            "scraper_type": "static",
            "token": "",
            "config": "{}",
            "enabled": 1,
            "created_at": batch["created_at"],
            "updated_at": batch["created_at"],
        }],
        "batches": [batch],
        "indexData": {"data": data, "columns": columns, "batch": batch},
        "periods": periods_payload(source),
        "favorites": favorites or [],
        "preferences": preferences or {},
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def payload_from_db(db_path: str | None = None) -> dict:
    import db

    if db_path:
        db.DB_PATH = db_path
    db.init_db()

    sources = db.get_sources(enabled_only=True)
    if not sources:
        raise RuntimeError("数据库里没有启用的数据源")
    source = sources[0]
    batch = db.get_latest_batch(source["id"])
    if not batch:
        raise RuntimeError("数据库里还没有批次数据")
    rows = db.get_index_data_by_batch(batch["id"])
    return make_payload(
        rows,
        batch,
        source=source,
        favorites=db.get_favorites(),
        preferences=db.get_all_preferences(),
    )


def payload_from_api() -> dict:
    cookies_raw = os.environ.get("LIXINGER_COOKIES", "").strip()
    if not cookies_raw:
        raise RuntimeError("缺少 LIXINGER_COOKIES 环境变量")
    source = json.loads((TOOLS / "source.json").read_text(encoding="utf-8"))

    fd, tmp_name = tempfile.mkstemp(prefix="lixinger_", suffix=".json")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(cookies_raw, encoding="utf-8")
        scraper.STATE_FILE = str(tmp)
        rows = scraper.fetch_via_api(source)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    if not rows:
        raise RuntimeError("接口没有返回数据")

    batch = {
        "id": 1,
        "source_id": 1,
        "batch_date": date.today().isoformat(),
        "status": "success",
        "record_count": len(rows),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    seed_path = TOOLS / "seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.exists() else {}
    return make_payload(
        rows,
        batch,
        source=source,
        favorites=seed.get("favorites", []),
        preferences=seed.get("preferences", {}),
    )


def inject_template(html: str) -> str:
    html = html.replace(
        '<meta charset="utf-8">',
        '<meta charset="utf-8">\n  <meta name="robots" content="noindex,nofollow">',
        1,
    )
    html = html.replace(
        "<title>指数数据小程序</title>",
        "<title>指数数据（在线版）</title>",
        1,
    )
    html = html.replace(
        '<script src="qrcode.js"></script>',
        '<script src="qrcode.js"></script>\n  <script src="static-app.js"></script>',
        1,
    )
    return html


def write_site(payload: dict | None, out_dir: Path, passphrase: str, encrypt: bool,
               fallback_data: Path | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    template = (TEMPLATE / "index.html").read_text(encoding="utf-8")
    (out_dir / "index.html").write_text(inject_template(template), encoding="utf-8")
    shutil.copy2(TEMPLATE / "qrcode.js", out_dir / "qrcode.js")
    shutil.copy2(SITE / "static-app.js", out_dir / "static-app.js")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    if payload is None:
        if not (fallback_data and fallback_data.exists()):
            raise RuntimeError("没有可用的兜底数据")
        shutil.copy2(fallback_data, out_dir / "data.enc.json")
        return

    if encrypt:
        if not passphrase:
            raise RuntimeError("缺少 SITE_PASSPHRASE")
        fd, tmp_name = tempfile.mkstemp(prefix="shuju_payload_", suffix=".json")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            env = os.environ.copy()
            env["SITE_PASSPHRASE"] = passphrase
            subprocess.run(
                ["node", str(TOOLS / "encrypt_data.js"), str(tmp), str(out_dir / "data.enc.json")],
                check=True,
                env=env,
            )
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
    else:
        with open(out_dir / "data.json", "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description="构建在线只读版")
    parser.add_argument("--db", action="store_true", help="从本机 SQLite 读取最新批次")
    parser.add_argument("--api", action="store_true", help="从理杏仁接口抓取")
    parser.add_argument("--db-path", default=None, help="SQLite 路径（默认使用项目 data/index_tool.db）")
    parser.add_argument("--out", default="docs", help="输出目录（默认 docs）")
    parser.add_argument("--no-encrypt", action="store_true", help="输出未加密的 data.json（仅本地调试）")
    parser.add_argument("--fallback", default="docs/data.enc.json", help="接口失败时使用的已加密数据")
    args = parser.parse_args()

    if not args.db and not args.api:
        parser.error("请指定 --db 或 --api")

    passphrase = os.environ.get("SITE_PASSPHRASE", "").strip()
    if args.api and not args.no_encrypt and not passphrase:
        print("ERROR: 缺少 SITE_PASSPHRASE 环境变量", file=sys.stderr)
        return 2

    out_dir = (ROOT / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    fallback = (ROOT / args.fallback).resolve() if not Path(args.fallback).is_absolute() else Path(args.fallback)

    try:
        payload = payload_from_api() if args.api else payload_from_db(args.db_path)
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: 获取数据失败：{exc}", file=sys.stderr)
        if fallback.exists():
            write_site(None, out_dir, passphrase, not args.no_encrypt, fallback_data=fallback)
            print(f"使用兜底数据部署：{fallback}")
            print(f"输出目录：{out_dir}")
            return 0
        print("ERROR: 没有可用的兜底数据", file=sys.stderr)
        return 1

    write_site(payload, out_dir, passphrase, not args.no_encrypt)
    batch = payload["batches"][0]
    print(f"数据日期：{batch['batch_date']} · {batch['record_count']} 条")
    print(f"输出目录：{out_dir}")
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
