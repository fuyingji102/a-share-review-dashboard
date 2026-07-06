/**
 * Netlify Function：东方财富资金流数据代理
 *
 * 浏览器由于 CORS 限制无法直接请求东方财富 API，
 * 此函数作为代理转发请求，返回资金流分钟数据。
 *
 * 调用方式：
 *   GET /.netlify/functions/fund-flow?mode=market
 *   GET /.netlify/functions/fund-flow?mode=sector
 */
const https = require("https");

// 东方财富 API 地址
const EAST_FLOW_URL =
  "https://push2delay.eastmoney.com/api/qt/stock/fflow/kline/get";
const EAST_RANK_URL = "https://push2delay.eastmoney.com/api/qt/clist/get";
const FUYAO_BASE_URL = "https://fuyao.aicubes.cn";

// 节流缓存（30秒内重复请求不重复调用东方财富）
const cache = { ttl: 30_000, data: {}, time: 0 };
function cached(key, fn) {
  const now = Date.now();
  if (cache.data[key] && now - cache.time < cache.ttl) {
    return cache.data[key];
  }
  cache.data[key] = fn();
  cache.time = now;
  return cache.data[key];
}

/** HTTPS GET 返回 JSON */
function httpsGet(url, params, extraHeaders = {}) {
  const qs = Object.entries(params || {})
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join("&");
  const fullUrl = `${url}?${qs}`;
  return new Promise((resolve, reject) => {
    const req = https.get(
        fullUrl,
        { headers: { "User-Agent": "Mozilla/5.0", Referer: "https://quote.eastmoney.com/", ...extraHeaders } },
        (res) => {
          let data = "";
          res.on("data", (c) => (data += c));
          res.on("end", () => {
            try {
              if ((res.statusCode || 500) >= 400) throw new Error(`HTTP ${res.statusCode}`);
              resolve(JSON.parse(data));
            } catch (e) {
              reject(new Error("Parse error"));
            }
          });
        }
      );
    req.setTimeout(8000, () => req.destroy(new Error("Upstream timeout")));
    req.on("error", reject);
  });
}

async function fuyaoGet(path, params) {
  const token = process.env.FUYAO_TOKEN || process.env.API_KEY;
  if (!token) throw new Error("FUYAO_TOKEN is not configured in Netlify environment variables");
  const envelope = await httpsGet(`${FUYAO_BASE_URL}${path}`, params, {
    "X-api-key": token,
    Accept: "application/json",
    Referer: FUYAO_BASE_URL,
  });
  if (envelope.code !== 0) throw new Error(`Fuyao ${envelope.code}: ${envelope.message}`);
  return envelope.data || {};
}

/** 获取单只标的分钟资金流 */
async function fetchFlowRows(secid) {
  const payload = await httpsGet(EAST_FLOW_URL, {
    lmt: "0", klt: "1", fields1: "f1,f2,f3,f7",
    fields2: "f51,f52,f53,f54,f55,f56,f57,f58",
    ut: "b2884a393a59ad64002292a3e90d46a5", secid,
  });
  const klines = (payload.data || {}).klines || [];
  return klines.map((line) => {
    const p = line.split(",");
    return {
      time: p[0],
      main: Number(p[1]) / 1e8,
      small: Number(p[2]) / 1e8,
      medium: Number(p[3]) / 1e8,
      big: Number(p[4]) / 1e8,
      super: Number(p[5]) / 1e8,
    };
  });
}

/** 获取板块排名 */
async function fetchSectorRanking(descending) {
  const payload = await httpsGet(EAST_RANK_URL, {
    fid: "f62", po: descending ? "1" : "0", pz: "8",
    pn: "1", np: "1", fltt: "2", invt: "2",
    fs: "m:90+t:2", fields: "f12,f14,f62",
  });
  const items = (payload.data || {}).diff || [];
  return items
    .map((item) => {
      const value = Number(item.f62 || 0) / 1e8;
      return { code: item.f12, name: item.f14, value };
    })
    .filter(
      (r) => (descending && r.value > 0) || (!descending && r.value < 0)
    )
    .slice(0, 8);
}

async function fetchSectorCatalog() {
  const groups = [
    ["industry", "行业", "m:90+t:2"],
    ["concept", "概念", "m:90+t:3"],
  ];
  const pages = await Promise.all(groups.map(async ([type, typeLabel, fs]) => {
    const params = {
      fid: "f62", po: "1", pz: "100", np: "1",
      fltt: "2", invt: "2", fs, fields: "f12,f14",
    };
    const first = await httpsGet(EAST_RANK_URL, { ...params, pn: "1" });
    const firstData = first.data || {}, total = Number(firstData.total || 0);
    const pageCount = Math.min(20, Math.max(1, Math.ceil(total / 100)));
    const rest = await Promise.all(Array.from({ length: Math.max(0, pageCount - 1) }, (_, i) =>
      httpsGet(EAST_RANK_URL, { ...params, pn: String(i + 2) })
    ));
    return [first, ...rest].flatMap((payload) => ((payload.data || {}).diff || []))
      .filter((item) => item.f12 && item.f14)
      .map((item) => ({ code: item.f12, name: item.f14, type, type_label: typeLabel }));
  }));
  return Array.from(new Map(pages.flat().map((row) => [row.code, row])).values())
    .sort((a, b) => `${a.type}${a.name}`.localeCompare(`${b.type}${b.name}`, "zh-CN"));
}

async function fetchSectorSeries(codes) {
  const valid = Array.from(new Set(codes.map((code) => String(code).toUpperCase())))
    .filter((code) => /^BK\d{4}$/.test(code)).slice(0, 8);
  const sectorSeries = {};
  await Promise.all(valid.map(async (code) => {
    try {
      const rows = await fetchFlowRows(`90.${code}`);
      sectorSeries[code] = rows.map((row) => ({ time: row.time, value: row.main }));
    } catch {
      sectorSeries[code] = [];
    }
  }));
  return { sector_series: sectorSeries, live: true };
}

async function fetchSectorConstituents(code) {
  const payload = await httpsGet(EAST_RANK_URL, {
    pn: "1", pz: "100", po: "1", np: "1", fltt: "2", invt: "2",
    fid: "f62", fs: `b:${code}`,
    fields: "f12,f14,f2,f3,f5,f6,f8,f10,f15,f16,f17,f18,f20,f21,f62",
  });
  return ((payload.data || {}).diff || []).map((item) => ({
    code: item.f12,
    name: item.f14,
    price: Number(item.f2 || 0),
    change_pct: Number(item.f3 || 0),
    volume: Number(item.f5 || 0),
    turnover: Number(item.f6 || 0),
    turnover_rate: Number(item.f8 || 0),
    volume_ratio: Number(item.f10 || 0),
    high: Number(item.f15 || 0),
    low: Number(item.f16 || 0),
    open: Number(item.f17 || 0),
    prev_close: Number(item.f18 || 0),
    market_cap: Number(item.f20 || 0),
    float_cap: Number(item.f21 || 0),
    main_net: Number(item.f62 || 0),
  }));
}

function combineMarketFlow(sh, sz) {
  const byTime = {};
  for (const source of [sh, sz]) {
    for (const item of source) {
      const row = byTime[item.time] || { time: item.time, main: 0, small: 0, medium: 0, big: 0, super: 0 };
      for (const field of ["main", "small", "medium", "big", "super"]) row[field] += item[field];
      byTime[item.time] = row;
    }
  }
  return Object.values(byTime).sort((a, b) => a.time.localeCompare(b.time));
}

async function fetchLiveOverview() {
  const [marketData, limitData, downData, hotData, sh, sz, topIn, topOut] = await Promise.all([
    fuyaoGet("/api/a-share/prices/snapshot", { limit: "10000", offset: "0" }),
    fuyaoGet("/api/a-share/special-data/limit-up-pool", { page: "1", size: "200", sort_field: "continue_day_cnt", sort_dir: "desc" }),
    fuyaoGet("/api/a-share/special-data/anomaly-analysis-list", { tag_codes: "LIMIT_DOWN" }),
    fuyaoGet("/api/a-share/special-data/hot-stock-list", { period: "day" }),
    fetchFlowRows("1.000001"), fetchFlowRows("0.399001"),
    fetchSectorRanking(true), fetchSectorRanking(false),
  ]);
  const stocks = marketData.item || [];
  const turnover = stocks.reduce((sum, row) => sum + Number(row.turnover || 0), 0);
  const upCount = stocks.filter((row) => Number(row.price_change_ratio_pct || 0) > 0).length;
  const downCount = stocks.filter((row) => Number(row.price_change_ratio_pct || 0) < 0).length;
  const flatCount = Math.max(0, stocks.length - upCount - downCount);
  const limitRows = limitData.item || [];
  const limitTotal = Number((limitData.pagination || {}).total || limitRows.length);
  const downRows = downData.item || [];
  const maxBoard = limitRows.reduce((max, row) => Math.max(max, Number(row.continue_day_cnt || 1)), 0);
  const breadth = upCount / Math.max(1, upCount + downCount);
  const temperature = Math.round(Math.max(0, Math.min(100, 50 + (breadth - 0.5) * 55 + Math.min(limitTotal, 100) * 0.15)));
  const marketRows = combineMarketFlow(sh, sz);
  const latest = marketRows[marketRows.length - 1] || {};
  const rawTimestamp = Number(marketData.timestamp || Date.now());
  const marketDate = new Date(rawTimestamp > 1e12 ? rawTimestamp : rawTimestamp * 1000).toLocaleDateString("en-CA", { timeZone: "Asia/Shanghai" });
  const quotes = Object.fromEntries(stocks.map((row) => [row.thscode, row]));
  const leaders = (hotData.item || []).slice(0, 8).map((row) => {
    const quote = quotes[row.thscode] || {};
    return { thscode: row.thscode, name: row.name || row.thscode, rank: row.rank, heat: row.heat, last_price: Number(quote.last_price || 0), change_pct: Number(quote.price_change_ratio_pct || 0), tags: row.tags || [], trend: [] };
  });
  return {
    live: true,
    meta: { market_date: marketDate, updated_at: new Date().toISOString(), errors: [] },
    market: { turnover, turnover_change_pct: null, up_count: upCount, down_count: downCount, flat_count: flatCount, sample_count: stocks.length, temperature },
    sentiment: { limit_up_count: limitTotal, limit_down_count: downRows.length, max_board: maxBoard, break_rate: null },
    fund_flow: { source: "东方财富 push2delay", market_rows: marketRows, market_latest: latest, sector_in: topIn, sector_out: topOut, sector_series: {}, sector_names: Object.fromEntries([...topIn, ...topOut].map((x) => [x.code, x.name])) },
    leaders,
  };
}

exports.handler = async (event) => {
  const mode = event.queryStringParameters.mode || "market";

  // CORS 头（允许 Netlify 前端调用）
  const headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Content-Type": "application/json",
  };
  if (event.httpMethod === "OPTIONS") {
    return { statusCode: 204, headers };
  }

  try {
    if (mode === "overview") {
      const overview = await cached("overview", fetchLiveOverview);
      return { statusCode: 200, headers, body: JSON.stringify(overview) };
    } else if (mode === "catalog") {
      const items = await cached("catalog", fetchSectorCatalog);
      return { statusCode: 200, headers, body: JSON.stringify({ items, source: "东方财富行业/概念板块目录", live: true }) };
    } else if (mode === "series") {
      const codes = String(event.queryStringParameters.codes || "").split(",");
      const payload = await fetchSectorSeries(codes);
      return { statusCode: 200, headers, body: JSON.stringify(payload) };
    } else if (mode === "market") {
      // ====== 大盘资金流 ======
      const [sh, sz, topIn, topOut] = await Promise.all([
        fetchFlowRows("1.000001"),
        fetchFlowRows("0.399001"),
        fetchSectorRanking(true),
        fetchSectorRanking(false),
      ]);

      // 合并沪深
      const byTime = {};
      for (const source of [sh, sz]) {
        for (const item of source) {
          const row = byTime[item.time] || {
            time: item.time, main: 0, small: 0, medium: 0, big: 0, super: 0,
          };
          row.main += item.main;
          row.small += item.small;
          row.medium += item.medium;
          row.big += item.big;
          row.super += item.super;
          byTime[item.time] = row;
        }
      }
      const marketRows = Object.values(byTime).sort((a, b) =>
        a.time.localeCompare(b.time)
      );
      const latest = marketRows[marketRows.length - 1] || {};

      // 板块分钟资金流
      const sectorItems = [...topIn, ...topOut];
      const sectorSeries = {};
      const sectorNames = {};
      await Promise.all(sectorItems.map(async (item) => {
        try {
          const rows = await fetchFlowRows(`90.${item.code}`);
          sectorSeries[item.code] = rows.map((r) => ({
            time: r.time,
            value: r.main,
          }));
          sectorNames[item.code] = item.name;
        } catch {
          sectorSeries[item.code] = [];
        }
      }));

      return {
        statusCode: 200,
        headers,
        body: JSON.stringify({
          source: "东方财富 push2delay",
          market_rows: marketRows,
          market_latest: latest,
          sector_in: topIn,
          sector_out: topOut,
          sector_series: sectorSeries,
          sector_names: sectorNames,
          live: true,
        }),
      };
    } else if (mode === "sector") {
      const code = String(event.queryStringParameters.code || "").toUpperCase();
      if (!/^BK\d{4}$/.test(code)) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: "invalid sector code" }) };
      }
      const [stocks, flow] = await Promise.all([
        fetchSectorConstituents(code),
        fetchFlowRows(`90.${code}`),
      ]);
      return {
        statusCode: 200,
        headers,
        body: JSON.stringify({
          code,
          name: event.queryStringParameters.name || code,
          stocks,
          flow: flow.map((row) => ({ time: row.time, value: row.main })),
          updated_at: flow.length ? flow[flow.length - 1].time : "",
          live: true,
        }),
      };
    } else {
      return { statusCode: 400, headers, body: JSON.stringify({ error: "unknown mode" }) };
    }
  } catch (err) {
    return {
      statusCode: 502,
      headers,
      body: JSON.stringify({ error: err.message, live: false }),
    };
  }
};
