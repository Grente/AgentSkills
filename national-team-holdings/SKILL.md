---
name: national-team-holdings
description: >-
  Computes an unofficial China A-share "national-team holdings" proxy: combined
  market cap of four CSI 300 ETFs (510300 Huatai-PB, 510330 ChinaAMC, 159919
  Harvest, 510310 E Fund) divided by CSI 300 free-float cap, as a percentage
  series. Use when the user asks for 国家队持仓, 汇金持仓, 国家队真实持仓,
  沪深300ETF 规模占比, CSI 300 ETF proxy holdings, or to summarize/export that
  ratio over 1 month, 3 months, 6 months, 1 year, or custom dates. Default
  output is data only (text/JSON/CSV), not charts. Never treat the number as official.
compatibility: Requires Python 3.10+, pip packages in requirements.txt, and network access to eastmoney.com, sse.com.cn, szse.cn, sina.com.cn, and qq.com/gtimg.cn.
---

# 国家队持仓代理统计

> **代理指标，非官方持仓。** 不是国家队 / 汇金的官方持仓明细，也不能从公开数据确认买方身份。只统计下面四只主流沪深300 ETF。

## 何时使用

- 用户要看「国家队」「汇金」通过主流沪深300 ETF 的**代理占比**
- 用户要缩短或拉长时间：最近 1 个月 / 3 个月 / 半年 / 1 年，或自定义起止日
- 需要摘要（最新占比、数据日、ETF 合计市值、沪深300流通市值、区间高低）或导出 JSON / CSV

**默认只输出数据**（终端摘要或 JSON/CSV）。不要主动出图或写 HTML。仅当用户明确要求网页/图表时，才加 `--html` 或 `--web`。

## 不要使用 / 不要声称

- 不要说这是官方持仓、真实持仓或监管披露
- 不要补上其它沪深300 ETF、股指期货、股票直投
- 接口失败时说明数据源与原因，**禁止编造数字**

## 公式

```
代理占比 = Σ(基金份额_i × 单位净值_i) / 沪深300流通市值 × 100%
```

| 代码 | 名称 | 市场 |
|------|------|------|
| 510300 | 华泰柏瑞沪深300ETF | 上交所 |
| 510330 | 华夏沪深300ETF | 上交所 |
| 159919 | 嘉实沪深300ETF | 深交所 |
| 510310 | 易方达沪深300ETF | 上交所 |

单位：

- 上交所 `TOT_VOL` 是**万份**，必须 ×10000 转为**份**
- 深交所「基金规模(份)」已经是**份**
- 市值用**单位净值（DWJZ）**，不用累计净值（LJJZ）
- 历史流通市值：`流通市值_t ≈ 最新流通市值 × 收盘_t / 收盘_最新`（近似）

## 时间窗口

可缩小或拉长。摘要、CSV、JSON、可选网页都只保留窗口内的点。

| 参数 | 含义 |
|------|------|
| `--period 1m` | 最近 1 个月（默认） |
| `--period 3m` | 最近 3 个月 |
| `--period 6m` | 最近半年 |
| `--period 1y` | 最近 1 年 |
| `--period ytd` | 年初至今 |
| `--start YYYY-MM-DD --end YYYY-MM-DD` | 自定义起止日（`--start` 视为自定义窗口） |
| `--end` + `--period` | 截止某日再往前推一个周期 |

未指定周期时用 `1m`。

## 输入 / 输出

默认 stdout 为中文摘要；`--format json` 则打 JSON。可选写文件：`--csv`、`--json`。

可选网页（默认关闭）：

```bash
python3 scripts/holdings.py --period 1m --html
python3 scripts/holdings.py --period 6m --web out.html
```

未传 `--html` / `--web` 时**不要**生成图表或 HTML。

JSON 字段包括：`disclaimer`、`ratio`、`data_date`、`etf_mv_sum_yi`、`csi300_circ_mv_yi`、`period`（含 preset/起止/点数）、`range.high/low`、`etfs`、`series`、`warnings`。

## 步骤

1. 向用户复述：这是**代理指标，非官方持仓**。
2. 解析时间窗口。未指定则 `--period 1m`。用户说「拉长到一年」用 `--period 1y`，说「最近三个月」用 `--period 3m`。
3. 依赖未装时，在技能目录：

   ```bash
   python3 -m pip install -r requirements.txt
   python3 scripts/holdings.py --period 1m
   python3 scripts/holdings.py --period 3m --format json
   python3 scripts/holdings.py --period 6m --csv --json
   python3 scripts/holdings.py --start 2026-04-01 --end 2026-10-28 --format json
   ```

4. 用脚本结果作答。进度在 stderr，正文在 stdout。Agent 解析数据时优先 `--format json`。
5. 失败则报告 `[数据源] 原因`，不要编数字。可 `--refresh`。缓存在 `data_cache/`（不要提交 git）。
6. 展示时同时给出数据日、合计市值、流通市值、窗口高低，并再次标明非官方。
7. 只有用户要网页或图表时才加 `--html` / `--web`。

## 数据来源

| 用途 | 来源 |
|------|------|
| 基金单位/累计净值 | 东财 `api.fund.eastmoney.com/f10/lsjz` |
| 上交所 ETF 份额 | `query.sse.com.cn/commonQuery.do`（TOT_VOL 万份） |
| 深交所 ETF 份额 | `www.szse.cn/api/report/ShowReport`（份） |
| 沪深300 日 K | 新浪 → 腾讯 → 东财 |
| 最新流通/总市值 | 腾讯 `qt.gtimg.cn/q=sh000300` → 东财 f117/f116 |

长区间上交所份额：双周采样 + 近 15 个交易日，其余前向填充。短于约 45 个交易日则按日请求。

## 局限

1. 代理指标，非官方持仓。
2. 仅四只 ETF。
3. 上交所份额非逐日全量（长区间）。
4. 历史流通市值是点位缩放近似。
5. 依赖公开接口稳定性。
