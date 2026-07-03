from __future__ import annotations

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"
CURRENT_FILE = DATA_DIR / "dashboard-data.json"
BASE_URL = "https://fuyao.aicubes.cn"
EAST_FLOW_URL = "https://push2delay.eastmoney.com/api/qt/stock/fflow/kline/get"
EAST_RANK_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
PORT = int(os.getenv("DASHBOARD_PORT", "8765"))

STATE: dict[str, Any] = {"refreshing": False, "message": "等待刷新", "updated_at": None}
STATE_LOCK = threading.Lock()
HTTP_SESSION = requests.Session()
# Codex's restricted command environment injects a deliberately dead local
# proxy. Ignore only that sentinel; preserve genuine user/corporate proxies.
if "127.0.0.1:9" in (os.getenv("HTTPS_PROXY", "") + os.getenv("https_proxy", "")):
    HTTP_SESSION.trust_env = False


def load_local_env() -> None:
    candidates = [
        ROOT / ".env",
        ROOT.parent.parent / "work" / "Financial-API" / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
        break


def api_get(path: str, params: dict[str, Any] | None = None, timeout: int = 30) -> Any:
    token = os.getenv("FUYAO_TOKEN") or os.getenv("API_KEY")
    if not token:
        raise RuntimeError("未找到 FUYAO_TOKEN/API_KEY；请先配置本地环境变量")
    response = HTTP_SESSION.get(
        f"{BASE_URL}{path}",
        params={k: v for k, v in (params or {}).items() if v is not None},
        headers={"X-api-key": token, "Accept": "application/json"},
        timeout=(8, timeout),
    )
    response.raise_for_status()
    envelope = response.json()
    if envelope.get("code") != 0:
        raise RuntimeError(f"API {envelope.get('code')}: {envelope.get('message')}")
    return envelope.get("data") or {}


def safe_call(name: str, path: str, params: dict[str, Any] | None = None) -> tuple[str, Any, str | None]:
    for attempt in range(2):
        try:
            return name, api_get(path, params), None
        except Exception as exc:  # keep the rest of the dashboard usable
            if "429" in str(exc) and attempt == 0:
                time.sleep(1.5)
                continue
            return name, None, str(exc)
    return name, None, "unknown request failure"


def fetch_index_group(tag: str) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    catalog_data = api_get("/api/a-share-index/catalog/ths-index-list", {"tag": tag})
    catalog = catalog_data.get("item", [])
    by_code = {row.get("thscode"): row for row in catalog if row.get("thscode")}
    # The catalog can contain hundreds of indices. Cap the first release to a
    # broad, deterministic universe and fetch batches concurrently so a slow
    # provider cannot make the whole dashboard wait for several minutes.
    codes = list(by_code)[:300]
    snapshots: list[dict[str, Any]] = []
    batches = [codes[offset : offset + 100] for offset in range(0, len(codes), 100)]
    for index, batch in enumerate(batches, 1):
        for attempt in range(2):
            try:
                data = api_get("/api/a-share-index/prices/snapshot", {"thscodes": ",".join(batch)}, 20)
                snapshots.extend(data.get("item", []))
                break
            except Exception as exc:
                if "429" in str(exc) and attempt == 0:
                    time.sleep(1.5)
                    continue
                errors.append(f"{tag} batch {index}: {exc}")
        time.sleep(0.35)
    rows: list[dict[str, Any]] = []
    for snap in snapshots:
        meta = by_code.get(snap.get("thscode"), {})
        rows.append(
            {
                "thscode": snap.get("thscode"),
                "name": meta.get("name") or meta.get("index_name") or snap.get("thscode"),
                "change_pct": number(snap.get("price_change_ratio_pct")),
                "turnover": number(snap.get("turnover")),
            }
        )
    rows.sort(key=lambda row: (row["change_pct"], row["turnover"]), reverse=True)
    return rows, errors


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def eastmoney_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    response = HTTP_SESSION.get(
        url,
        params=params,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        timeout=(8, 20),
    )
    response.raise_for_status()
    return json.loads(response.content.decode("utf-8"))


def fetch_flow_rows(secid: str) -> list[dict[str, Any]]:
    payload = eastmoney_get(
        EAST_FLOW_URL,
        {
            "lmt": "0", "klt": "1", "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ut": "b2884a393a59ad64002292a3e90d46a5", "secid": secid,
        },
    )
    rows = []
    for line in ((payload.get("data") or {}).get("klines") or []):
        parts = line.split(",")
        rows.append({
            "time": parts[0], "main": number(parts[1]) / 1e8,
            "small": number(parts[2]) / 1e8, "medium": number(parts[3]) / 1e8,
            "big": number(parts[4]) / 1e8, "super": number(parts[5]) / 1e8,
        })
    return rows


def fetch_sector_ranking(descending: bool) -> list[dict[str, Any]]:
    payload = eastmoney_get(
        EAST_RANK_URL,
        {
            "fid": "f62", "po": "1" if descending else "0", "pz": "8",
            "pn": "1", "np": "1", "fltt": "2", "invt": "2",
            "fs": "m:90+t:2", "fields": "f12,f14,f62",
        },
    )
    result = []
    for item in ((payload.get("data") or {}).get("diff") or []):
        value = number(item.get("f62")) / 1e8
        if (descending and value <= 0) or (not descending and value >= 0):
            continue
        result.append({"code": item.get("f12"), "name": item.get("f14"), "value": value})
    return result[:8]


def fetch_eastmoney_fund_flow() -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=4) as executor:
        sh_future = executor.submit(fetch_flow_rows, "1.000001")
        sz_future = executor.submit(fetch_flow_rows, "0.399001")
        in_future = executor.submit(fetch_sector_ranking, True)
        out_future = executor.submit(fetch_sector_ranking, False)
        market_sources = [sh_future.result(), sz_future.result()]
        top_in, top_out = in_future.result(), out_future.result()

    market_by_time: dict[str, dict[str, Any]] = {}
    for source in market_sources:
        for item in source:
            row = market_by_time.setdefault(item["time"], {"time": item["time"], "main": 0, "small": 0, "medium": 0, "big": 0, "super": 0})
            for field in ("main", "small", "medium", "big", "super"):
                row[field] += item[field]
    market_rows = [market_by_time[key] for key in sorted(market_by_time)]

    sector_items = top_in + top_out
    sector_series: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        pending = {executor.submit(fetch_flow_rows, f"90.{item['code']}"): item for item in sector_items}
        for future in as_completed(pending):
            item = pending[future]
            try:
                sector_series[item["code"]] = [{"time": row["time"], "value": row["main"]} for row in future.result()]
            except Exception:
                sector_series[item["code"]] = []
    latest = market_rows[-1] if market_rows else {}
    return {
        "source": "东方财富 push2delay", "market_rows": market_rows,
        "market_latest": latest, "sector_in": top_in, "sector_out": top_out,
        "sector_series": sector_series,
        "sector_names": {item["code"]: item["name"] for item in sector_items},
    }


def items(value: Any) -> list[dict[str, Any]]:
    return value.get("item", []) if isinstance(value, dict) else []


def daily_lhb_rows(payload: Any, field: str) -> list[dict[str, Any]]:
    rows = payload.get("stock_items", []) if isinstance(payload, dict) else []
    daily = [row for row in rows if int(number(row.get("range_days"), 1)) == 1]
    daily.sort(key=lambda row: number(row.get(field)), reverse=True)
    return daily


def load_history() -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    if HISTORY_DIR.exists():
        for path in sorted(HISTORY_DIR.glob("*.json"))[-30:]:
            try:
                history.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
    return history


def compact_history(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_date": snapshot["meta"]["trade_date"],
        "updated_at": snapshot["meta"]["updated_at"],
        "market": snapshot["market"],
        "sentiment": snapshot["sentiment"],
        "industry_top": snapshot["industry_top"][:5],
        "concept_top": snapshot["concept_top"][:5],
        "leaders": [
            {"thscode": row.get("thscode"), "name": row.get("name"), "last_price": row.get("last_price"), "change_pct": row.get("change_pct")}
            for row in snapshot["leaders"]
        ],
    }


def build_snapshot(raw: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    market_rows = raw.get("market", {}).get("item", []) if isinstance(raw.get("market"), dict) else []
    turnover = sum(number(row.get("turnover")) for row in market_rows)
    up_count = sum(number(row.get("price_change_ratio_pct")) > 0 for row in market_rows)
    down_count = sum(number(row.get("price_change_ratio_pct")) < 0 for row in market_rows)
    flat_count = max(0, len(market_rows) - up_count - down_count)

    limit_payload = raw.get("limit_up") or {}
    limit_rows = items(limit_payload)
    limit_total = int(number((limit_payload.get("pagination") or {}).get("total"), len(limit_rows)))
    max_board = max([int(number(row.get("continue_day_cnt"), 1)) for row in limit_rows] or [0])
    limit_down_rows = items(raw.get("limit_down") or {})
    ladder_rows = items(raw.get("ladder") or {})
    ladder_series: list[dict[str, Any]] = []
    for day in reversed(ladder_rows):
        boards = day.get("boards") or {}
        heights = [int(number(stock.get("board_num"))) for group in boards.values() for stock in (group or [])]
        ladder_series.append({"date": day.get("date"), "height": max(heights or [0])})

    org_rows = daily_lhb_rows(raw.get("org") or {}, "org_net_value")
    hot_money_rows = daily_lhb_rows(raw.get("hot_money") or {}, "hot_money_net_value")
    org_total = sum(number(row.get("org_net_value")) for row in org_rows)
    hot_money_total = sum(number(row.get("hot_money_net_value")) for row in hot_money_rows)

    full_by_code = {row.get("thscode"): row for row in market_rows}
    hot_rows = items(raw.get("hot") or {})[:10]
    leaders: list[dict[str, Any]] = []
    for row in hot_rows[:8]:
        quote = full_by_code.get(row.get("thscode"), {})
        leaders.append(
            {
                "thscode": row.get("thscode"),
                "name": row.get("name") or row.get("thscode"),
                "rank": row.get("rank"),
                "heat": row.get("heat"),
                "last_price": number(quote.get("last_price")),
                "change_pct": number(quote.get("price_change_ratio_pct")),
                "tags": row.get("tags") or [],
            }
        )

    lhb_date = (raw.get("org") or {}).get("trade_date")
    market_timestamp = number((raw.get("market") or {}).get("timestamp"))
    market_seconds = market_timestamp / 1000 if market_timestamp > 10_000_000_000 else market_timestamp
    market_date = datetime.fromtimestamp(market_seconds).astimezone().strftime("%Y-%m-%d") if market_seconds else datetime.now().strftime("%Y-%m-%d")
    trade_date = market_date
    previous = load_history()
    prev_market = previous[-1].get("market", {}) if previous else {}
    prev_turnover = number(prev_market.get("turnover"))
    turnover_change_pct = ((turnover / prev_turnover - 1) * 100) if turnover and prev_turnover else None

    breadth_ratio = up_count / max(1, up_count + down_count)
    score = 50 + (breadth_ratio - 0.5) * 50 + min(limit_total, 100) * 0.15
    if turnover_change_pct is not None:
        score += max(-10, min(10, turnover_change_pct * 0.8))
    score = round(max(0, min(100, score)))

    industry_top = raw.get("industry_rows", [])[:10]
    concept_top = raw.get("concept_rows", [])[:10]
    fund_latest = (raw.get("fund_flow") or {}).get("market_latest") or {}
    main_flow_net = number(fund_latest.get("main")) * 1e8 if fund_latest else None
    focus = {
        "industries": [row["name"] for row in industry_top[:3]],
        "concepts": [row["name"] for row in concept_top[:3]],
        "stocks": [row["name"] for row in org_rows[:3] if number(row.get("org_net_value")) > 0],
        "note": "观察强势方向能否获得成交额与龙头走势确认；不是买卖建议。",
    }

    return {
        "meta": {"trade_date": trade_date, "market_date": market_date, "lhb_date": lhb_date, "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "source": "同花顺扶摇 Financial API", "errors": errors, "has_data": bool(market_rows or limit_rows or org_rows or hot_money_rows or industry_top or concept_top)},
        "market": {"turnover": turnover, "turnover_change_pct": turnover_change_pct, "up_count": up_count, "down_count": down_count, "flat_count": flat_count, "sample_count": len(market_rows), "temperature": score},
        "money_flow": {"main_net": main_flow_net, "status": "东方财富沪深两市分钟主力净额" if main_flow_net is not None else "资金流补充源暂不可用", "industry_basis": "同花顺行业指数涨幅 + 成交额代理", "concept_basis": "同花顺概念指数涨幅 + 成交额代理"},
        "industry_top": industry_top,
        "concept_top": concept_top,
        "sentiment": {"limit_up_count": limit_total, "limit_down_count": len(limit_down_rows), "max_board": max_board, "break_rate": None, "ladder": ladder_series[-20:]},
        "structure": {"org_net": org_total, "hot_money_net": hot_money_total, "etf_net": None, "scope": "龙虎榜当日口径（range_days=1）"},
        "org_top": [normalize_lhb(row, "org_net_value") for row in org_rows[:10]],
        "hot_money_top": [normalize_lhb(row, "hot_money_net_value") for row in hot_money_rows[:10]],
        "leaders": leaders,
        "focus": focus,
        "fund_flow": raw.get("fund_flow") or {"source": "东方财富 push2delay", "error": "本轮未取得资金流数据"},
    }


def normalize_lhb(row: dict[str, Any], field: str) -> dict[str, Any]:
    concepts = [entry.get("name") for entry in (row.get("concept_list") or []) if isinstance(entry, dict)]
    return {"thscode": row.get("thscode"), "name": row.get("name") or row.get("thscode"), "net": number(row.get(field)), "change_pct": number(row.get("change")) * 100, "concepts": concepts[:3]}


def collect() -> dict[str, Any]:
    load_local_env()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    jobs = {
        "market": ("/api/a-share/prices/snapshot", {"limit": 10000, "offset": 0}),
        "limit_up": ("/api/a-share/special-data/limit-up-pool", {"page": 1, "size": 200, "sort_field": "continue_day_cnt", "sort_dir": "desc"}),
        "ladder": ("/api/a-share/special-data/limit-up-ladder", {}),
        "limit_down": ("/api/a-share/special-data/anomaly-analysis-list", {"tag_codes": "LIMIT_DOWN"}),
        "hot": ("/api/a-share/special-data/hot-stock-list", {"period": "day"}),
        "org": ("/api/a-share/special-data/dragon-tiger-list", {"board_type": "org"}),
        "hot_money": ("/api/a-share/special-data/dragon-tiger-list", {"board_type": "hot_money"}),
    }
    raw: dict[str, Any] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(safe_call, name, path, params) for name, (path, params) in jobs.items()]
        fund_future = executor.submit(fetch_eastmoney_fund_flow)
        for future in as_completed(futures):
            name, value, error = future.result()
            raw[name] = value or {}
            if error:
                errors.append(f"{name}: {error}")
        try:
            raw["fund_flow"] = fund_future.result()
        except Exception as exc:
            raw["fund_flow"] = {"source": "东方财富 push2delay", "error": str(exc)}
            errors.append(f"fund_flow: {exc}")

    for tag, key in [("industry", "industry_rows"), ("cn_concept", "concept_rows")]:
        try:
            rows, group_errors = fetch_index_group(tag)
            raw[key] = rows
            errors.extend(group_errors)
        except Exception as exc:
            raw[key] = []
            errors.append(f"{key}: {exc}")

    snapshot = build_snapshot(raw, errors)
    history = load_history()
    today_compact = compact_history(snapshot)
    prior = [row for row in history if row.get("trade_date") != today_compact["trade_date"]]
    combined = (prior + [today_compact])[-30:]
    snapshot["rotation"] = [
        {"date": row.get("trade_date"), "industries": [x.get("name") for x in row.get("industry_top", [])[:3]], "concepts": [x.get("name") for x in row.get("concept_top", [])[:3]]}
        for row in combined
    ]
    for leader in snapshot["leaders"]:
        leader["trend"] = [
            {"date": row.get("trade_date"), "price": next((number(x.get("last_price")) for x in row.get("leaders", []) if x.get("thscode") == leader.get("thscode")), None)}
            for row in combined
        ]
        leader["trend"] = [point for point in leader["trend"] if point["price"] is not None]

    # Never replace a successful prior close with a totally empty failed pull.
    if not snapshot["meta"]["has_data"] and CURRENT_FILE.exists():
        try:
            previous_current = json.loads(CURRENT_FILE.read_text(encoding="utf-8"))
            if previous_current.get("meta", {}).get("has_data"):
                return previous_current
        except Exception:
            pass
    CURRENT_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    if snapshot["meta"]["has_data"]:
        (HISTORY_DIR / f"{today_compact['trade_date']}.json").write_text(json.dumps(today_compact, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


def refresh_background() -> None:
    with STATE_LOCK:
        if STATE["refreshing"]:
            return
        STATE.update(refreshing=True, message="正在并行采集行情、板块与龙虎榜…")
    try:
        snapshot = collect()
        message = "刷新完成" if not snapshot["meta"]["errors"] else f"刷新完成，{len(snapshot['meta']['errors'])} 个数据项降级"
        with STATE_LOCK:
            STATE.update(message=message, updated_at=snapshot["meta"]["updated_at"])
    except Exception as exc:
        with STATE_LOCK:
            STATE["message"] = f"刷新失败：{exc}"
    finally:
        with STATE_LOCK:
            STATE["refreshing"] = False


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/data":
            self.send_json(json.loads(CURRENT_FILE.read_text(encoding="utf-8")) if CURRENT_FILE.exists() else {"empty": True})
            return
        if self.path == "/api/status":
            with STATE_LOCK:
                self.send_json(dict(STATE))
            return
        if self.path == "/":
            self.path = "/dashboard.html"
        super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/api/refresh":
            self.send_error(404)
            return
        with STATE_LOCK:
            busy = STATE["refreshing"]
        if not busy:
            threading.Thread(target=refresh_background, daemon=True).start()
        self.send_json({"accepted": not busy, "refreshing": True})

    def send_json(self, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{datetime.now():%H:%M:%S}] {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="A股资金与轮动 · 盘后复盘台")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="监听地址（设为 0.0.0.0 以允许局域网访问）")
    args = parser.parse_args()
    os.chdir(ROOT)
    load_local_env()
    print(f"A股盘后复盘看板：http://{args.bind}:{PORT}")
    print("页面打开后点击“刷新数据”；首次采集可能需要 1–3 分钟。")
    if args.bind != "127.0.0.1":
        print(f"局域网访问地址：http://{_local_ip()}:{PORT}")
    ThreadingHTTPServer((args.bind, PORT), Handler).serve_forever()


def _local_ip() -> str:
    """获取本机局域网 IP（用于提示）"""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "你的IP"


if __name__ == "__main__":
    main()
