"""
数据源适配器 — 从理杏仁获取指数估值数据（含全部 6 个分位点周期）。

主路径：直接调用理杏仁页面「导出CSV」所用的同一接口
        POST https://www.lixinger.com/api/ii/mix-metrics/indices/latest
        一次拿到 上市以来/20年/10年/5年/3年/1年 六个周期的分位点数据。
回退路径：接口不可用时，回退到 Playwright 逐周期导出 CSV。

字段命名约定：
    周期无关列  PE-TTM(当前值)、涨跌幅、收盘点位、股息率(当前值)、净资产收益率(ROE)(2025Q4)
    周期相关列  PE-TTM(分位点%·5年)、PE-TTM(20%分位点值·5年)、PB(最小值·上市以来) …
"""

import csv
import io
import json
import logging
import os

logger = logging.getLogger(__name__)

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(MODULE_DIR, "data", "lixinger_state.json")

API_URL = "https://www.lixinger.com/api/ii/mix-metrics/indices/latest"

# ── 分位点周期（与理杏仁「估值列表」页面上的按钮一一对应）──────────
PERIODS = [
    ("y1", "1年"),
    ("y3", "3年"),
    ("y5", "5年"),
    ("y10", "10年"),
    ("y20", "20年"),
    ("fs", "上市以来"),
]
DEFAULT_PERIOD = "y10"
PERIOD_LABELS = dict(PERIODS)

# ── 估值指标：接口 key → 列名前缀 ─────────────────────────────
METRICS = [
    ("pe_ttm", "PE-TTM"),
    ("pb", "PB"),
    ("ps_ttm", "PS-TTM"),
    ("dyr", "股息率"),
]

# ── 分位点窗口相关子字段：接口 key → 列名限定词 ────────────────
BANDS = [
    ("minv", "最小值"),
    ("q2v", "20%分位点值"),
    ("q5v", "50%分位点值"),
    ("q8v", "80%分位点值"),
    ("maxpv", "最大正值"),
    ("cvpos", "分位点%"),
]

PRICE_METRICS = ["pe_ttm", "pb", "ps_ttm", "dyr", "cpc", "cp"]

LOGIN_HINT = (
    "未找到理杏仁登录状态。请先运行一次:\n"
    "  python login_helper.py\n"
    "登录理杏仁后即可使用「更新数据」功能。"
)


class LoginError(RuntimeError):
    """登录态缺失或已过期。"""


# ═══════════════════════════════════════════════════════════
# 配置读取
# ═══════════════════════════════════════════════════════════

def _source_config(source: dict) -> dict:
    cfg = source.get("config") if isinstance(source, dict) else None
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except (json.JSONDecodeError, TypeError):
            cfg = None
    return cfg if isinstance(cfg, dict) else {}


def get_periods(source: dict = None) -> list[tuple]:
    """返回本次需要抓取的分位点周期列表 [(key, label), …]，默认周期排在最前。"""
    cfg = _source_config(source or {})
    keys = cfg.get("percentile_periods")
    if not isinstance(keys, list) or not keys:
        keys = [k for k, _ in PERIODS]
    picked = [(k, PERIOD_LABELS[k]) for k in keys if k in PERIOD_LABELS]
    if not picked:
        picked = [(k, v) for k, v in PERIODS]
    default = cfg.get("default_period") or DEFAULT_PERIOD
    if default not in dict(picked):
        default = picked[0][0]
    # 默认周期排第一位，保证「当前值」等周期无关字段有值
    picked.sort(key=lambda kv: 0 if kv[0] == default else 1)
    return picked


def get_default_period(source: dict = None) -> str:
    periods = get_periods(source)
    return periods[0][0] if periods else DEFAULT_PERIOD


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def fetch_index_data(source: dict) -> list[dict]:
    """获取指数估值数据（含全部分位点周期）。"""
    periods = get_periods(source)
    try:
        return fetch_via_api(source, periods)
    except LoginError:
        raise
    except Exception as e:  # noqa: BLE001 — 其它失败退回浏览器导出
        logger.warning("理杏仁接口抓取失败，回退到 Playwright 导出 CSV：%s", e)
        return fetch_via_playwright(source, periods)


# ═══════════════════════════════════════════════════════════
# 路径一：内部接口（推荐）
# ═══════════════════════════════════════════════════════════

def _load_cookies() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        raw = json.load(open(STATE_FILE, "r", encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(raw, dict):  # 兼容 {name: value} 形式
        return raw
    return {
        c["name"]: c["value"]
        for c in raw
        if isinstance(c, dict) and "lixinger" in str(c.get("domain", "")) and c.get("name")
    }


def _require_cookies() -> dict:
    cookies = _load_cookies()
    if not cookies:
        raise LoginError(LOGIN_HINT)
    return cookies


def fetch_via_api(source: dict, periods: list = None) -> list[dict]:
    """调用理杏仁内部接口，一次抓取全部分位点周期并合并成行。"""
    try:
        import requests
    except ImportError:
        raise ImportError("缺少 requests 依赖，请运行: pip install requests")

    periods = periods or get_periods(source)
    cookies = _require_cookies()

    cfg = _source_config(source)
    items_by_period = {}
    for key, label in periods:
        items_by_period[key] = _request_period(requests, cookies, key, cfg)

    return _merge_periods(items_by_period, periods, cfg)


def _request_period(requests, cookies: dict, granularity: str, cfg: dict) -> dict:
    """请求单个分位点周期，返回 {指数代码: item}。"""
    metrics_type = cfg.get("metrics_type", "mcw")
    body = {
        "granularity": granularity,
        "priceMetricsType": metrics_type,
        "hotMetricsTypes": ["cp"],
        "source": cfg.get("source", "all"),
        "series": cfg.get("series", "all"),
        "groupIds": cfg.get("group_ids", "all"),
        "fsMetricsNames": ["roe"],
        "fsMetricsType": metrics_type,
        "priceMetricsNames": cfg.get("price_metrics", PRICE_METRICS),
        "sortName": "pm.pe_ttm.cv",
        "sortOrder": "asc",
    }
    headers = {
        "Content-Type": "application/json",
        "Referer": "https://www.lixinger.com/analytics/index/dashboard/value",
        "User-Agent": "Mozilla/5.0",
    }

    last_error = None
    for attempt in (1, 2):
        try:
            resp = requests.post(
                API_URL, json=body, headers=headers, cookies=cookies, timeout=120
            )
            if resp.status_code in (401, 403):
                raise LoginError("理杏仁登录已过期，请重新运行 「登录理杏仁.py」")
            if resp.status_code != 200:
                raise RuntimeError(f"接口返回 HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            if isinstance(data, dict):  # 带分页时返回 {total, list}
                data = data.get("list", [])
            if not isinstance(data, list) or not data:
                raise RuntimeError("接口返回空数据")
            return {
                str(item["stock"]["stockCode"]): item
                for item in data
                if isinstance(item, dict) and isinstance(item.get("stock"), dict)
            }
        except LoginError:
            raise
        except Exception as e:  # noqa: BLE001
            last_error = e
            logger.warning("分位点周期 %s 抓取失败（第 %s 次）: %s", granularity, attempt, e)
    raise RuntimeError(f"分位点周期 {granularity} 抓取失败: {last_error}")


# ═══════════════════════════════════════════════════════════
# 接口数据 → 应用字段
# ═══════════════════════════════════════════════════════════

def _num(value, digits: int = 4) -> str:
    """数值统一格式化为字符串，保持与 CSV 导出一致的显示精度。"""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _base_fields(item: dict, metrics_type: str = "mcw") -> dict:
    """周期无关字段：涨跌幅、收盘点位、ROE、关注度等。"""
    pm = item.get("pm") or {}
    hm = item.get("hm") or {}
    stock = item.get("stock") or {}
    fields = {
        "涨跌幅": _num(pm.get("cpc")),
        "收盘点位": _num(pm.get("cp")),
        "今年以来涨跌幅": _num(hm.get("cpc_fys")),
        "关注度": _num(stock.get("followedNum")),
    }
    roe_table = (((item.get("fsm") or {}).get(metrics_type) or {}).get("m") or {}).get("roe") or {}
    for date_key, value in sorted((roe_table.get("t") or {}).items(), reverse=True):
        quarter = _quarter_label(date_key)
        if quarter:
            fields[f"净资产收益率(ROE)({quarter})"] = _num(value)
    return {k: v for k, v in fields.items() if v != ""}


def _quarter_label(date_key: str) -> str:
    """'2025-12-31' → '2025Q4'。"""
    parts = str(date_key).split("-")
    if len(parts) < 2:
        return ""
    month = int(parts[1]) if parts[1].isdigit() else 0
    quarter = (month + 2) // 3
    return f"{parts[0]}Q{quarter}" if quarter else ""


def _beijing_date(iso_value) -> str:
    """接口返回的是 UTC 时间，转成北京时间取日期（与网页导出 CSV 一致）。"""
    text = str(iso_value or "")
    if not text:
        return ""
    try:
        from datetime import datetime, timedelta, timezone
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt.astimezone(timezone(timedelta(hours=8)))).strftime("%Y-%m-%d")
    except ValueError:
        return text[:10]


def _merge_periods(items_by_period: dict, periods: list, cfg: dict) -> list[dict]:
    """把各周期的接口结果合并为「一行一个指数」的结构。"""
    primary_key = periods[0][0]
    metrics_type = cfg.get("metrics_type", "mcw")
    primary = items_by_period.get(primary_key) or {}

    rows = []
    for code, item in primary.items():
        stock = item.get("stock") or {}
        fields = dict(_base_fields(item, metrics_type))

        for key, label in periods:
            period_item = items_by_period.get(key, {}).get(code)
            if not period_item:
                continue
            for mkey, mlabel in METRICS:
                # 「当前值」与周期无关，取第一个有值的周期
                cv_col = f"{mlabel}(当前值)"
                if cv_col not in fields:
                    cv = _band_value(period_item, mkey, key, metrics_type, "cv")
                    if cv is not None:
                        fields[cv_col] = _num(cv)
                for bkey, blabel in BANDS:
                    value = _band_value(period_item, mkey, key, metrics_type, bkey)
                    if value is not None:
                        fields[f"{mlabel}({blabel}·{label})"] = _num(value)

        launch = _beijing_date(stock.get("launchDate"))
        rows.append({
            "index_code": code,
            "index_name": stock.get("name") or code,
            "data_date": launch,
            "fields": fields,
        })
    return rows


def _band_value(item: dict, metric: str, granularity: str, metrics_type: str, band: str):
    statistics = ((item.get("pm") or {}).get("statistics") or {}).get(metric) or {}
    node = (statistics.get(granularity) or {}).get(metrics_type) or {}
    entry = node.get(band)
    if isinstance(entry, dict):
        return entry.get("value")
    return None


# ═══════════════════════════════════════════════════════════
# 路径二：Playwright 逐周期导出 CSV（回退）
# ═══════════════════════════════════════════════════════════

def fetch_via_playwright(source: dict, periods: list = None) -> list[dict]:
    """
    打开理杏仁页面，逐一切换分位点周期并点击「导出CSV」下载数据。
    首次使用前需运行 login_helper.py 保存登录态。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "Playwright 未安装。运行:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )

    url = source.get("url", "")
    if not url:
        raise ValueError("数据源缺少 URL")

    periods = periods or get_periods(source)
    if not os.path.exists(STATE_FILE):
        raise LoginError(LOGIN_HINT)

    merged: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            try:
                cookies = json.load(open(STATE_FILE, "r", encoding="utf-8"))
                context.add_cookies(cookies)
            except Exception:
                pass

            page.goto(url, wait_until="networkidle", timeout=120000)
            page.wait_for_timeout(3000)

            for key, label in periods:
                try:
                    _select_period(page, label)
                    rows = _export_csv(page, label)
                except Exception as e:  # noqa: BLE001 — 单个周期失败不影响其它周期
                    logger.warning("周期 %s 导出失败: %s", label, e)
                    continue
                _merge_rows(merged, rows)
        finally:
            browser.close()

    if not merged:
        raise RuntimeError("未能从理杏仁导出任何数据")

    rows = list(merged.values())
    for idx, row in enumerate(rows):
        row["_order"] = idx
    return rows


def _select_period(page, label: str):
    """点击分位点周期按钮（按可见文本精确匹配）。"""
    clicked = page.evaluate(
        """(label) => {
            const nodes = [...document.querySelectorAll('label.btn, button.btn')];
            const target = nodes.find(el => (el.innerText || '').trim() === label);
            if (!target) return false;
            target.click();
            return true;
        }""",
        label,
    )
    if not clicked:
        raise RuntimeError(f"未找到「{label}」周期按钮")
    page.wait_for_timeout(2500)


def _export_csv(page, label: str) -> list[dict]:
    """点击「导出CSV」并解析下载内容，字段按该周期打标签。"""
    with page.expect_download(timeout=90000) as download_info:
        page.click("button:has-text('导出CSV')", timeout=15000)
    download = download_info.value
    path = download.path()
    if not path:
        raise RuntimeError("下载文件为空")
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        content = f.read()
    reader = csv.DictReader(io.StringIO(content))
    csv_rows = [row for row in reader if any(str(v or "").strip() for v in row.values())]
    return _parse_csv_rows(csv_rows, label)


def _merge_rows(merged: dict, rows: list):
    """按指数代码合并多个周期的行，空值不覆盖已有值。"""
    for row in rows:
        code = row["index_code"]
        if code not in merged:
            merged[code] = row
            continue
        target = merged[code]["fields"]
        for k, v in row["fields"].items():
            if v and not target.get(k):
                target[k] = v
        if not merged[code].get("data_date"):
            merged[code]["data_date"] = row.get("data_date", "")


# ═══════════════════════════════════════════════════════════
# CSV 解析
# ═══════════════════════════════════════════════════════════

def _parse_csv_rows(csv_rows: list[dict], period_label: str = None) -> list[dict]:
    """将理杏仁 CSV 行转为标准格式，周期相关列追加「·周期」后缀。"""
    result = []
    for row in csv_rows:
        code_key = _find_key(row, ["指数代码", "代码", "stockCode", "indexCode"])
        name_key = _find_key(row, ["指数名称", "名称", "简称", "name", "indexName"])
        date_key = _find_key(row, ["发布时间", "日期", "更新时间", "date"])

        index_code = _clean(row.get(code_key, "")) if code_key else ""
        index_name = _clean(row.get(name_key, "")) if name_key else ""
        data_date = _clean(row.get(date_key, "")) if date_key else ""

        if "数据来源" in index_code or "数据来源" in index_name:
            continue
        if not index_code and not index_name:
            continue
        if not index_name:
            index_name = index_code
        if not index_code:
            index_code = index_name

        fields = {}
        for k, v in row.items():
            if k in (code_key, name_key, date_key):
                continue
            cleaned = _clean(v)
            if cleaned:
                fields[period_column(_clean(k), period_label)] = cleaned

        result.append({
            "index_code": index_code,
            "index_name": index_name,
            "data_date": data_date,
            "fields": fields,
        })
    return result


def period_column(column: str, period_label: str = None) -> str:
    """给周期相关的列名补上「·周期」后缀。

    PE-TTM(分位点%) → PE-TTM(分位点%·10年)
    PE-TTM(当前值) / 涨跌幅 / 净资产收益率(ROE)(2025Q4) 保持原样。
    """
    if not period_label:
        return column
    for _, blabel in BANDS:
        suffix = f"({blabel})"
        if column.endswith(suffix):
            return f"{column[:-len(suffix)]}({blabel}·{period_label})"
    return column


# ═══════════════════════════════════════════════════════════
# 数据清洗工具
# ═══════════════════════════════════════════════════════════

def _clean(value) -> str:
    """清洗单元格值：去 BOM、去等号、去引号包裹。"""
    if value is None:
        return ""
    text = str(value).strip()
    text = text.replace("\ufeff", "")
    if text.startswith("="):
        text = text[1:].strip()
    if (text.startswith('"') and text.endswith('"')) or \
       (text.startswith("'") and text.endswith("'")):
        text = text[1:-1]
    return text.strip()


def _find_key(row: dict, candidates: list[str]) -> str | None:
    """在行字典中查找第一个匹配的键名（精确 + 模糊）。"""
    for c in candidates:
        if c in row:
            return c
    for key in row:
        for c in candidates:
            if c in key:
                return key
    return None
