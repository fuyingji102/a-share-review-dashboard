#!/usr/bin/env python3
"""
独立数据采集器 · 运行一次即退出
用于 Windows 定时任务，每日盘后自动采集行情快照。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保能找到同目录的 app.py
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app import collect  # noqa: E402


def main() -> int:
    try:
        snapshot = collect()
    except Exception as exc:
        print(f"[失败] 采集抛出异常：{exc}")
        return 1

    meta = snapshot.get("meta", {})
    errors = meta.get("errors", [])
    trade_date = meta.get("trade_date", "未知")
    has_data = meta.get("has_data", False)

    if not has_data:
        print(f"[警告] 交易日期 {trade_date} · 本轮未取得有效数据（可能非交易日或 API 限流）")
        return 0  # 不视为失败，可能是非交易日

    if errors:
        print(f"[完成] {trade_date} · 采集完成，{len(errors)} 个数据项降级")
        for err in errors[:5]:
            print(f"  ⚠  {err}")
        if len(errors) > 5:
            print(f"  … 还有 {len(errors)-5} 个错误")
    else:
        print(f"[完成] {trade_date} · 采集成功，无降级")

    # 打印关键摘要
    market = snapshot.get("market", {})
    sentiment = snapshot.get("sentiment", {})
    print(f"  成交额：{market.get('turnover', 0)/1e8:.0f}亿  "
          f"温度：{market.get('temperature')}  "
          f"涨停：{sentiment.get('limit_up_count')}  "
          f"跌停：{sentiment.get('limit_down_count')}  "
          f"最高连板：{sentiment.get('max_board')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
