---
name: national-team-holdings
description: >-
  Reports several unofficial China A-share "national-team" statistics side by
  side: (1) CSI 300 ETF proxy ratio = combined MV of 510300/510330/159919/510310
  over CSI 300 free-float cap; (2/3) daily ETF share change and estimated net
  creation/redemption; (6) latest semi-annual/annual top-10 holders matching
  Huijin / CSFC / NSSF aliases. Use for 国家队持仓, 汇金持仓, 国家队真实持仓,
  十大持有人, 净申赎, 沪深300ETF 规模占比. Default output is multi-stat data
  (text/JSON/CSV), not charts. Never treat any series as official; never mix in
  northbound/Stock Connect.
compatibility: Requires Python 3.10+, pip packages in requirements.txt, and network access to eastmoney.com, sse.com.cn, szse.cn, sina.com.cn, stockstar.com, and qq.com/gtimg.cn.
---

# 国家队持仓多口径对照

> **多口径公开数据，非官方持仓。** 默认同时列出几种互补统计，让用户自己判断。任一序列都不是国家队 / 汇金的官方持仓。北向 / 沪深股通 / 香港中央结算**不是**国家队，不要混入。

## 何时使用

- 用户要看「国家队」「汇金」相关的**可复核公开数据**（不只一个数字）
- 需要把 **代理占比、净申赎、定期报告十大持有人** 放在一起对照
- 用户要缩短或拉长**日频**窗口：最近 1 个月 / 3 个月 / 半年 / 1 年，或自定义起止日
- 需要摘要或导出 JSON / CSV

**默认只输出数据**（终端摘要或 JSON/CSV），且必须是**多口径并列**。不要主动出图或写 HTML。仅当用户明确要求网页/图表时，才加 `--html` 或 `--web`。

## 何时用哪一口径

| 口径 | 适合问什么 | 不适合 |
|------|------------|--------|
| **方法1 代理占比** | 「这四只沪深300 ETF 规模占沪深300流通市值多少？」日频趋势 | 识别买方；当作真实持仓比例 |
| **方法2/3 份额与净申赎** | 「最近是在申购还是赎回？规模变了多少？」剔除价格噪音 | 识别谁在申赎；长窗口上交所采样日以外的「每日」变化 |
| **方法6 十大持有人** | 「汇金/证金/社保在定期报告里持有多少？」最接近真实持有**水平** | 日频、实时；季报匿名「机构1/2」；未进前十视为 0 |

`--period` / `--start` / `--end` **只裁剪日频序列**（方法1、方法2/3）。方法6 默认取各 ETF **已披露的最新中报/年报**，或用 `--report-date YYYY-MM-DD` 指定，与日频窗口独立。

## 不要使用 / 不要声称

- 不要说任一口径是官方持仓、真实持仓或监管披露的国家队统计
- 不要把北向资金、沪深股通、香港中央结算算作国家队
- 不要用方法1 或净申赎代替「汇金持了多少」
- 接口失败时说明数据源与原因，**禁止编造数字**

## 公式

### 方法1 代理占比

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

### 方法2/3 份额变动与净申赎

```
Δ份额_t = 份额_t − 份额_{t-1}
净申赎额_t ≈ Δ份额_t × 单位净值_t     （显示为亿元）
```

摘要给出：最新一日净申赎、所选窗口累计净申赎、份额首末日（剔除价格噪音的规模趋势）。

长区间上交所份额为双周采样 + 近 15 个交易日 + 前向填充：非采样日 Δ份额为 0，采样日为该间隔内累计变化。短于约 45 个交易日则按日请求。

### 方法6 定期报告十大持有人

从半年报/年报「前十名持有人」中筛选名称匹配下列别名的记录（及常见变体）：

`中央汇金` / `汇金资管` / `汇金投资` / `中国证券金融` / `证金公司` / `全国社保` / `社保基金` / `社会保障基金` / `基本养老保险`

明确排除：`香港中央结算`（沪深股通）。

```
市值估算 = 持有份额 × 报告期末单位净值（缺则用最新单位净值）
```

季报通常只披露单一投资者持有 ≥20% 的情况，且常匿名为「机构1/2」；具名十大持有人在中报/年报最完整。未进入前十 **不等于** 持仓为零。滞后可达约 6 个月，**不是日频**。

可选 `--holders-basket wide`：在四只沪深300 ETF 之外增加常见汇金重仓宽基（510050 上证50、159915 创业板、510500 中证500、512100 中证1000、588000 科创50）。方法1/2/3 仍只用四只沪深300 ETF。

## 时间窗口

日频窗口可缩小或拉长。方法1、方法2/3 的摘要、CSV、JSON、可选网页都只保留窗口内的点。方法6 报告期另选。

| 参数 | 含义 |
|------|------|
| `--period 1m` | 最近 1 个月（默认，仅日频） |
| `--period 3m` | 最近 3 个月 |
| `--period 6m` | 最近半年 |
| `--period 1y` | 最近 1 年 |
| `--period ytd` | 年初至今 |
| `--start YYYY-MM-DD --end YYYY-MM-DD` | 自定义起止日（`--start` 视为自定义窗口） |
| `--end` + `--period` | 截止某日再往前推一个周期 |
| `--report-date YYYY-MM-DD` | 方法6 指定中报/年报报告期；默认最新已披露期 |
| `--holders-basket csi300\|wide` | 方法6 篮子，默认 `csi300` |

未指定周期时日频用 `1m`。

## 输入 / 输出

默认 stdout 为中文**多口径**摘要；`--format json` 则打 JSON。可选写文件：`--csv`、`--json`。

可选网页（默认关闭）：

```bash
python3 scripts/holdings.py --period 1m --html
python3 scripts/holdings.py --period 6m --web out.html
```

未传 `--html` / `--web` 时**不要**生成图表或 HTML。

JSON 顶层仍保留方法1 字段（`ratio`、`data_date`、`etf_mv_sum_yi` 等）以兼容旧解析；同时必须有 `methods`：

- `methods.proxy_ratio`：占比、ETF 合计市值、沪深300流通市值、`data_date`、`accuracy_note`
- `methods.net_creation`：最新一日/窗口净申赎与 Δ份额、`data_date`、`accuracy_note`
- `methods.disclosed_holders`：匹配持有人、报告期、来源 URL/API、`accuracy_note`
- `series`、`warnings`、`disclaimer`

每个口径保留自己的 `data_date` 或 `report_date` 以及一句准确度说明。

## 步骤

1. 向用户复述：这是**多口径公开对照，非官方持仓**；把几组数字一并给出，不要只报一个代理占比。
2. 解析日频时间窗口。未指定则 `--period 1m`。用户说「拉长到一年」用 `--period 1y`。方法6 不要用这个窗口去裁剪报告期，除非用户点名某一期中报/年报。
3. 依赖未装时，在技能目录：

   ```bash
   python3 -m pip install -r requirements.txt
   python3 scripts/holdings.py --period 1m
   python3 scripts/holdings.py --period 3m --format json
   python3 scripts/holdings.py --period 6m --csv --json
   python3 scripts/holdings.py --start 2026-04-01 --end 2026-10-28 --format json
   python3 scripts/holdings.py --report-date 2025-06-30 --format json
   python3 scripts/holdings.py --holders-basket wide --period 1m
   ```

4. 用脚本结果作答。进度在 stderr，正文在 stdout。Agent 解析数据时优先 `--format json`，并同时展示 `methods` 下三块。
5. 失败则报告 `[数据源] 原因`，不要编数字。方法6 失败时仍应输出方法1 与净申赎（若已算成），并写明警告。可 `--refresh`。缓存在 `data_cache/`（不要提交 git）。
6. 展示时每个口径都带数据日/报告期和准确度一句，并再次标明非官方。
7. 只有用户要网页或图表时才加 `--html` / `--web`。

## 数据来源

| 用途 | 来源 |
|------|------|
| 基金单位/累计净值 | 东财 `api.fund.eastmoney.com/f10/lsjz` |
| 上交所 ETF 份额 | `query.sse.com.cn/commonQuery.do`（TOT_VOL 万份） |
| 深交所 ETF 份额 | `www.szse.cn/api/report/ShowReport`（份） |
| 沪深300 日 K | 新浪 → 腾讯 → 东财 |
| 最新流通/总市值 | 腾讯 `qt.gtimg.cn/q=sh000300` → 东财 f117/f116 |
| 十大持有人（主） | 新浪 `CaihuiFundInfoService.getFundHolder` / `getFundHolderDate` |
| 十大持有人（备） | 证券之星 `fund.stockstar.com/funds/f10/fundholder_{code}.html` |

东财基金档案 `FundArchivesDatas.aspx?type=cyrjg` 只有机构/个人/内部持有比例，**没有**具名十大持有人，不能当方法6 的持有人名单。

长区间上交所份额：双周采样 + 近 15 个交易日，其余前向填充。短于约 45 个交易日则按日请求。

## 局限

1. 全部为公开数据对照，非官方持仓。
2. 方法1/2/3 默认仅四只沪深300 ETF。
3. 上交所份额非逐日全量（长区间），净申赎在非采样日为 0。
4. 历史流通市值是点位缩放近似。
5. 方法6 滞后约半年；未进前十≠0；季报常匿名。
6. 新浪份额有时四舍五入，证券之星备源更精确。
7. 依赖公开接口稳定性。
