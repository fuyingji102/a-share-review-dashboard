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
function httpsGet(url, params) {
  const qs = Object.entries(params || {})
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join("&");
  const fullUrl = `${url}?${qs}`;
  return new Promise((resolve, reject) => {
    const req = https.get(
        fullUrl,
        { headers: { "User-Agent": "Mozilla/5.0", Referer: "https://quote.eastmoney.com/" } },
        (res) => {
          let data = "";
          res.on("data", (c) => (data += c));
          res.on("end", () => {
            try {
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
    if (mode === "market") {
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
