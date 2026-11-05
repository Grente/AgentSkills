# AgentSkills

个人可复用的 Agent 技能合集。每个技能是一个独立目录，尽量遵循常见 [Agent Skill](https://agentskills.io/specification) 约定：目录内有 `SKILL.md`（何时用、输入输出、步骤、局限），以及可运行的脚本或说明。

把某个技能目录交给 Cursor / Claude 等 Agent 后，即可按 `SKILL.md` 调用，而不必把实现嵌进聊天记录。

## 已有技能

| 目录 | 说明 |
|------|------|
| [AINote](./AINote/) | 占位目录。现有说明为：AI 生成、从小红书 / 抖音等渠道收集的技能，内容待补充。 |
| [national-team-holdings](./national-team-holdings/) | **国家队持仓代理统计**（非官方）。按可选时间窗口计算四只沪深300 ETF 合计市值占沪深300流通市值的比例；默认输出数据（摘要 / JSON / CSV），可选 `--html` 生成网页。 |

---

## 国家队持仓代理统计

目录：[`national-team-holdings/`](./national-team-holdings/)

### 做什么

按指定时间窗口拉取公开数据，得到一条**代理占比**序列，并给出：

- 最新代理占比与数据日
- 四只 ETF 合计市值、沪深300流通市值
- 该窗口内最高 / 最低
- 默认：终端摘要，或 `--format json` / `--csv` / `--json` 导出数据
- 可选：`--html` 或 `--web` 生成带图表的网页（**未传则不生成图或 HTML**）

时间窗口可缩小或拉长，摘要和导出都只保留窗口内的点：

```bash
cd national-team-holdings
python3 -m pip install -r requirements.txt

python3 scripts/holdings.py --period 1m          # 最近 1 个月（默认）
python3 scripts/holdings.py --period 3m          # 最近 3 个月
python3 scripts/holdings.py --period 6m          # 最近半年
python3 scripts/holdings.py --period 1y          # 最近 1 年
python3 scripts/holdings.py --start 2026-04-01 --end 2026-10-28
python3 scripts/holdings.py --period 1m --html   # 仅在需要网页时加上
```

### 不做什么

- **不是**官方国家队 / 汇金持仓，也不是监管披露的持仓明细
- 不覆盖其它沪深300 ETF、股指期货、股票直投
- 不能从公开数据确认这四只 ETF 的买方是谁
- 接口失败时会说明原因，不编造数字
- 默认不出图；不要把网页当成必选产物

> **代理指标，非官方持仓。** 详细免责声明、单位换算与局限见技能目录 README / `SKILL.md`。

### 公式

```
代理占比 = Σ(基金份额 × 单位净值) / 沪深300流通市值 × 100%
```

四只 ETF：`510300` 华泰柏瑞、`510330` 华夏、`159919` 嘉实、`510310` 易方达。

- 上交所份额接口 `TOT_VOL` 为**万份**，需 ×10000 转为份；深交所已是份
- 市值用**单位净值**，不用累计净值
- 历史流通市值用最新流通市值按沪深300收盘价等比缩放（近似）

### 数据来源

东财基金净值、上交所 / 深交所 ETF 份额、新浪或腾讯或东财的沪深300 日 K、腾讯或东财的最新流通市值。允许本地缓存。

给 Agent 使用时，请读取 [`national-team-holdings/SKILL.md`](./national-team-holdings/SKILL.md)。更完整的安装、参数与样例见 [`national-team-holdings/README.md`](./national-team-holdings/README.md)。
