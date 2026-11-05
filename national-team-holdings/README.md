# 国家队持仓代理统计

> **代理指标，非官方国家队 / 汇金持仓。**  
> 买方身份无法从公开数据确认。仅统计四只主流沪深300 ETF，未计入其它沪深300 ETF、股票直投等。

本目录是可复用的 Agent Skill：按指定时间窗口拉取公开数据，计算代理占比序列，**默认只输出数据**（终端摘要 / JSON / CSV）。  
算法口径对照常见「四只沪深300 ETF 合计规模 / 沪深300流通市值」做法（小红书等处或称「国家队真实持仓」，该名称容易误导，这里只称**代理指标**）。

未传入 `--html` / `--web` 时，**不生成图表或 HTML**。

## 公式

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

## 时间窗口

窗口可缩小或拉长。摘要、CSV、JSON、可选网页都按该窗口**裁剪**序列后再算区间高低。

| 用法 | 含义 |
|------|------|
| `--period 1m` | 最近 1 个月（默认） |
| `--period 3m` | 最近 3 个月 |
| `--period 6m` | 最近半年 |
| `--period 1y` | 最近 1 年 |
| `--period ytd` | 年初至今 |
| `--start 2026-04-01 --end 2026-10-28` | 自定义起止日 |
| `--period 1m --end 2026-06-30` | 截止某日再往前一个月 |

传入 `--start` 即视为自定义窗口。

## 依赖与运行

需要 Python 3.10+，以及访问东财、上交所、深交所、新浪/腾讯行情的网络。

```bash
cd national-team-holdings
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 默认：最近一个月，只打印摘要（不出图、不写 HTML）
.venv/bin/python scripts/holdings.py --period 1m

# 拉长到半年，并导出数据文件
.venv/bin/python scripts/holdings.py --period 6m --csv --json

# 三个月，stdout 为 JSON（方便 Agent 解析）
.venv/bin/python scripts/holdings.py --period 3m --format json

# 自定义起止日
.venv/bin/python scripts/holdings.py --start 2026-04-01 --end 2026-10-28

# 可选：只要网页时才加 --html 或 --web
.venv/bin/python scripts/holdings.py --period 1y --html
.venv/bin/python scripts/holdings.py --period 1m --web report.html
```

常用参数：

| 参数 | 含义 |
|------|------|
| `--period 1m\|3m\|6m\|1y\|ytd` | 预设窗口，默认 `1m` |
| `--start` / `--end` | 自定义日期（`--end` 默认为上海时区今天） |
| `--format text\|json` | stdout 格式，默认 text |
| `--csv` `--json` | 导出数据文件；只写开关时用当前目录默认文件名 |
| `--html` / `--web` | **可选**网页（可含图表）。不传则不生成图或 HTML |
| `--cache-dir` | 缓存目录，默认本技能下 `data_cache/` |
| `--refresh` | 重拉净值/行情（保留已缓存的份额日期） |
| `--clear-cache` | 清空缓存后全量重拉 |
| `--quiet` | 不在 stderr 打进度 |

首次拉取可能需要一两分钟（上交所按日查询较慢）；之后走缓存会快很多。  
**不要把 `data_cache/`、`.venv/`、生成的 HTML 提交进 git。**

失败时脚本退出码为 1，并打印 `[数据源] 原因`，例如净值接口 JSON 解析失败、深交所未返回 Excel、流通市值全部备用源失败等。

## 样例快照

`examples/sample_summary.json` 是一次真实拉取的**摘要快照**（约 2026-11-04），不是实时行情：

| 项目 | 数值（快照） |
|------|----------------|
| 代理比例 | 0.8765% |
| 四只 ETF 合计市值 | 约 4277 亿 |
| 沪深300流通市值 | 约 488017 亿 |
| 沪深300收盘 | 约 4101 |

完整 CLI 还会给出所选窗口内的最高/最低与逐日序列。

公式与窗口裁剪可用无网络单测核对：

```bash
python3 tests/test_formula.py
```

## 数据来源

| 用途 | 端点 |
|------|------|
| 基金历史净值 | `https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}` |
| 上交所 ETF 份额 | `https://query.sse.com.cn/commonQuery.do`（`TOT_VOL` 万份） |
| 深交所 ETF 份额 | `https://www.szse.cn/api/report/ShowReport`（`CATALOGID=scsj_fund_jjgm`，份） |
| 沪深300 K 线 | 新浪 `CN_MarketData.getKLineData?symbol=sh000300`；腾讯日 K、东财 push2his 备用 |
| 最新流通/总市值 | 腾讯 `https://qt.gtimg.cn/q=sh000300`（亿元）；东财 `f116`/`f117` 备用 |

长区间上交所份额：双周采样 + 近 15 个交易日全量，其余前向填充。短于约 45 个交易日则按日请求。

## 局限

1. **代理指标，非官方持仓。**
2. 仅 4 只 ETF。
3. 上交所份额非逐日全量（长区间）。
4. 历史流通市值按点位缩放近似。
5. 依赖公开接口稳定性；被反爬或改版时请看报错里的数据源名称。

给 Agent 的调用说明见同目录 [`SKILL.md`](./SKILL.md)。
