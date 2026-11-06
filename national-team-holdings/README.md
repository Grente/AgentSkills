# 国家队持仓多口径对照

> **多口径公开数据，非官方国家队 / 汇金持仓。**  
> 默认把几种互补统计并列输出，由使用者自行判断。买方身份无法从日频公开数据确认。  
> **北向 / 沪深股通 / 香港中央结算不是国家队，不计入方法6。**

本目录是可复用的 Agent Skill：按指定**日频**时间窗口拉取公开数据，并同时给出：

1. **方法1** 四只主流沪深300 ETF 合计市值 / 沪深300流通市值（原有代理占比）
2. **方法2/3** 日份额变动与估算净申赎（亿元）
3. **方法6** 最新（或指定）半年报/年报「十大持有人」中汇金 / 证金 / 社保等披露持仓

**默认只输出数据**（终端摘要 / JSON / CSV），且必须是多口径。未传入 `--html` / `--web` 时，**不生成图表或 HTML**。

算法口径对照常见「四只沪深300 ETF 合计规模 / 沪深300流通市值」做法（小红书等处或称「国家队真实持仓」，该名称容易误导）。方法6 更接近具名持有**水平**，但滞后，不是日频。

## 何时看哪一口径

| 口径 | 适合 | 局限 |
|------|------|------|
| 方法1 代理占比 | 日频规模占流通市值的趋势 | 无法识别买方 |
| 方法2/3 净申赎 | 申购/赎回方向与规模变化（剔除价格） | 不能识别谁在申赎；长窗口上交所为采样 |
| 方法6 十大持有人 | 汇金等主体在中报/年报中的持有份额与占比 | 滞后约 6 个月；未进前十≠0；季报常匿名 |

`--period` 只作用于方法1 与方法2/3。方法6 报告期默认「各 ETF 已披露的最新中报/年报」，用 `--report-date` 另行指定。

## 公式

### 方法1

```
代理占比 = Σ(基金份额 × 单位净值) / 沪深300流通市值 × 100%
```

| 代码 | 名称 | 市场 |
|------|------|------|
| 510300 | 华泰柏瑞沪深300ETF | 上交所 |
| 510330 | 华夏沪深300ETF | 上交所 |
| 159919 | 嘉实沪深300ETF | 深交所 |
| 510310 | 易方达沪深300ETF | 上交所 |

### 单位核对

- **上交所份额**（`TOT_VOL`）：接口单位为 **万份**，代码内 ×10000 → **份**。
- **深交所份额**：报表「基金规模(份)」已是 **份**。
- **市值** = 份额(份) × **单位净值**(元/份) → 元，再 ÷1e8 显示为「亿」。
- 与东方财富「基金规模」口径一致。累计净值在这四只上常因拆分/合并偏低，**不用累计净值算市值**。

### 流通市值历史

公开接口通常只给最新流通市值。历史点：

```
流通市值_t ≈ 流通市值_最新 × 收盘_t / 收盘_最新
```

指数成分调整会使除数变化，因此历史比例是**趋势近似**，最新点相对可靠。

### 方法2/3

```
净申赎额_t ≈ (份额_t − 份额_{t-1}) × 单位净值_t
```

窗口累计为窗口内逐日加总。份额首末日反映规模趋势，不受净值涨跌干扰。

长区间上交所份额：双周采样 + 近 15 个交易日，其余前向填充 → 非采样日 Δ份额为 0。短于约 45 个交易日则按日请求。

### 方法6

筛选前十名持有人名称中含 中央汇金 / 汇金资管 / 汇金投资 / 中国证券金融 / 证金公司 / 全国社保 / 社保基金 / 社会保障基金 / 基本养老保险 等别名的记录。

```
市值估算 = 持有份额 × 报告期末单位净值（缺则用最新净值）
```

排除香港中央结算。同一报告期才把四只 ETF 的匹配市值加总当作可对照合计；报告期不一致时会注明。

可选 `--holders-basket wide` 增加 510050、159915、510500、512100、588000（公开报道中较常见的汇金重仓宽基）。方法1/2/3 仍只用上表四只。

东财 `cyrjg` 页只有机构/个人持有比例，没有具名十大持有人。

## 时间窗口

日频窗口可缩小或拉长。方法1、净申赎、CSV 日频列都按该窗口**裁剪**。方法6 不受此窗口裁剪。

| 用法 | 含义 |
|------|------|
| `--period 1m` | 最近 1 个月（默认，仅日频） |
| `--period 3m` | 最近 3 个月 |
| `--period 6m` | 最近半年 |
| `--period 1y` | 最近 1 年 |
| `--period ytd` | 年初至今 |
| `--start 2026-04-01 --end 2026-10-28` | 自定义起止日 |
| `--period 1m --end 2026-06-30` | 截止某日再往前一个月 |
| `--report-date 2025-12-31` | 方法6 指定报告期 |
| `--holders-basket wide` | 方法6 扩展宽基篮子 |

传入 `--start` 即视为自定义日频窗口。

## 依赖与运行

需要 Python 3.10+，以及访问东财、上交所、深交所、新浪/腾讯行情、证券之星的网络。

```bash
cd national-team-holdings
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 默认：最近一个月日频 + 最新中报/年报持有人，只打印多口径摘要（不出图）
.venv/bin/python scripts/holdings.py --period 1m

# 拉长到半年，并导出数据文件
.venv/bin/python scripts/holdings.py --period 6m --csv --json

# 三个月，stdout 为 JSON（方便 Agent 解析 methods.*）
.venv/bin/python scripts/holdings.py --period 3m --format json

# 指定方法6 报告期（与 --period 独立）
.venv/bin/python scripts/holdings.py --period 1m --report-date 2025-06-30

# 方法6 加上常见汇金重仓宽基
.venv/bin/python scripts/holdings.py --holders-basket wide

# 自定义起止日
.venv/bin/python scripts/holdings.py --start 2026-04-01 --end 2026-10-28

# 可选：只要网页时才加 --html 或 --web
.venv/bin/python scripts/holdings.py --period 1y --html
.venv/bin/python scripts/holdings.py --period 1m --web report.html
```

常用参数：

| 参数 | 含义 |
|------|------|
| `--period 1m\|3m\|6m\|1y\|ytd` | 日频窗口，默认 `1m` |
| `--start` / `--end` | 自定义日频日期（`--end` 默认为上海时区今天） |
| `--report-date` | 方法6 报告期；默认最新已披露中报/年报 |
| `--holders-basket csi300\|wide` | 方法6 篮子，默认 `csi300` |
| `--format text\|json` | stdout 格式，默认 text |
| `--csv` `--json` | 导出数据文件；只写开关时用当前目录默认文件名 |
| `--html` / `--web` | **可选**网页（可含图表）。不传则不生成图或 HTML |
| `--cache-dir` | 缓存目录，默认本技能下 `data_cache/` |
| `--refresh` | 重拉净值/行情/持有人（忽略缓存时效） |
| `--clear-cache` | 清空缓存后全量重拉 |
| `--quiet` | 不在 stderr 打进度 |

首次拉取可能需要一两分钟（上交所按日查询较慢）；之后走缓存会快很多。  
**不要把 `data_cache/`、`.venv/`、生成的 HTML 提交进 git。**

方法1 所需的净值/份额若全部失败，脚本退出码为 1。方法6 单独失败时仍输出方法1 与净申赎，并在 `warnings` 写明 `[数据源] 原因`，**不编造**持有人数字。

## 样例快照

`examples/sample_summary.json` 是一次真实拉取的**方法1 摘要快照**（约 2026-11-04），不是实时行情。完整 CLI 现在还会给出净申赎与方法6 持有人，JSON 见 `methods.proxy_ratio` / `methods.net_creation` / `methods.disclosed_holders`。

| 项目 | 数值（快照，仅方法1） |
|------|----------------|
| 代理比例 | 0.8765% |
| 四只 ETF 合计市值 | 约 4277 亿 |
| 沪深300流通市值 | 约 488017 亿 |
| 沪深300收盘 | 约 4101 |

公式、窗口裁剪、持有人解析与净申赎可用无网络单测核对：

```bash
python3 tests/test_formula.py tests/test_netflow.py tests/test_holders.py
```

或：

```bash
python3 -m unittest discover -s tests -v
```

## 数据来源

| 用途 | 端点 |
|------|------|
| 基金历史净值 | `https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}` |
| 上交所 ETF 份额 | `https://query.sse.com.cn/commonQuery.do`（`TOT_VOL` 万份） |
| 深交所 ETF 份额 | `https://www.szse.cn/api/report/ShowReport`（`CATALOGID=scsj_fund_jjgm`，份） |
| 沪深300 K 线 | 新浪 `CN_MarketData.getKLineData?symbol=sh000300`；腾讯日 K、东财 push2his 备用 |
| 最新流通/总市值 | 腾讯 `https://qt.gtimg.cn/q=sh000300`（亿元）；东财 `f116`/`f117` 备用 |
| 十大持有人（主） | `https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/CaihuiFundInfoService.getFundHolder`（`symbol`,`date`）；报告期列表 `getFundHolderDate` |
| 十大持有人页面 | `https://stock.finance.sina.com.cn/fundInfo/view/FundInfo_JJCYR.php?symbol={code}` |
| 十大持有人（备） | `https://fund.stockstar.com/funds/f10/fundholder_{code}.html` |

长区间上交所份额：双周采样 + 近 15 个交易日全量，其余前向填充。短于约 45 个交易日则按日请求。

## 局限

1. **全部非官方持仓。**
2. 方法1/2/3 仅 4 只 ETF。
3. 上交所份额非逐日全量（长区间），净申赎在非采样日为 0。
4. 历史流通市值按点位缩放近似。
5. 方法6 滞后；未进前十≠0；季报常不具名。
6. 新浪持有份额有时四舍五入。
7. 依赖公开接口稳定性；被反爬或改版时请看报错里的数据源名称。

给 Agent 的调用说明见同目录 [`SKILL.md`](./SKILL.md)。
