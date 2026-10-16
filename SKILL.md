---
name: jiangxi-bank-briefing
slug: jiangxi-bank-briefing
version: "1.0.0"
displayName: 江西政企研究简报（银行业版）
summary: 面向银行业金融机构的江西政企研究晨报、周报、月报与专题追踪；按岗位视角编排，支持自定义研究方向与关注池，逐条可追溯原文。
homepage: https://github.com/zleo-ai/jiangxi-bank-briefing
description: 为在江西展业的银行业金融机构编制可追溯的公开政企研究简报：晨报、周报、月报和专题追踪，覆盖江西省、11个设区市及县区的政策文件、重大项目、环评与招采公示、企业公告和金融同业动态。当银行员工要按自己的岗位（公司金融、普惠、科技金融、绿色金融、风险、授信审批、计划财务、交易银行、行领导、一线客户经理等或自定义岗位）看江西政企动态，设置或维护关注池（重点企业、项目、园区、专题、政策），追踪关注池对象进展，或把简报导出为PNG长图时使用。只处理公开信息，关注池不得包含授信、余额、账户、联系人等内部客户信息；不输出授信审批意见、营销线索或投资建议。
agent_created: true
metadata: {"version":"1.0.0","parent":"regional-enterprise-briefing 2.0.0","runtime":"Python 3.10+ optional; PNG export additionally requires Playwright and a local Chromium-family browser","os":["darwin","linux","win32"],"network":"Host public web search and original-page reading required for live collection"}
---

# 江西政企研究简报（银行业版）

为在江西展业的银行业金融机构，把公开的政策文件、产业项目、企业公告和金融同业动态编成可追溯的研究简报。每条事实带原始链接、发布日期和核验状态；按使用者的**岗位视角**排序展开，按**自定义研究方向**和**关注池**聚焦。交付自包含HTML和证据JSON，按需导出PNG长图。只生成本地文件，不代替授信审批、营销决策或对外发布。

本技能是 `regional-enterprise-briefing` 的江西银行业子集：地区固定为江西，阅读视角只保留银行岗位，新增岗位画像与关注池。证据门、写作规范和模板与父技能一致。

## 快速路径

| 用户说 | 你做 |
|---|---|
| “出一份昨天的江西政企晨报” | 江西全省、默认视角“分行综合”，直接编制 |
| “我是普惠条线的，看赣州上周动态” | `audience=inclusive`，地区赣州市，周报 |
| “我做园区金融服务，主要看开发区政策和园区企业投产” | `audience=custom`，`role_profile.title` 与 `focus_directions` 按原话填写 |
| “把这几家企业和两个园区加入关注池” | 核实全称后写入 `watch_pool`，先确认是公开研究范围（`public_ok=true`） |
| “我有一个Excel名单” | 请用户另存为“CSV UTF-8”，用 `pool-import` 转换（第2步） |
| “关注池里的企业最近一个月有什么动静” | 专题简报 `kind=special`，`scope_mode=all`，只看关注池命中 |
| “导出长图” | 第7步 `export_png.py` |

## 1. 范围、岗位画像与关注池

先读[配置规范](references/configuration.md)与[关注池指南](references/watch-pool.md)。只在用户明确的工作目录读取 `briefing-config.json`，不搜索主目录、通讯录、行内系统或内部客户档案。

- **地区**：固定在江西。未指定时为江西省全省；可选11个设区市（南昌、景德镇、萍乡、九江、新余、鹰潭、赣州、吉安、宜春、抚州、上饶）及其县区。用户提到省外地区时说明本技能只覆盖江西，可改用父技能 `regional-enterprise-briefing`。
- **岗位视角**：按[岗位视角目录](references/periodic-analysis.md#岗位视角目录)映射到22种银行岗位；目录外的岗位用 `custom` 并填写 `role_profile.title`。视角只调整排序、展开深度和跟踪问题，不改变事实、不扩大地域，页面不显示部门或机构抬头。
- **岗位画像** `role_profile`：用户用自己的话描述的研究方向（`focus_directions`，最多12项）、想回答的问题（`key_questions`，最多10项）和少看的内容（`deprioritize`）。方向用于编辑排序和速览取舍，并显示在报头“关注方向”；它**不是筛选条件**，要硬过滤请用 `topics` 与 `require_topic_match`。
- **关注池** `watch_pool`：用户自定义的企业、项目、园区、专题和政策，最多300项，每项带优先级与标签。它是**公开研究范围**，不是客户名单：
  - 每项必须 `public_ok=true`，表示用户确认可以拿它做公开检索；
  - 不得记录授信、额度、余额、存款、账户、联系人、电话、“我行/本行客户”等内部信息，工具会直接拒绝；
  - 用户声明名单是内部客户名单时，不提交外部检索，请用户另给可公开研究的范围。
- **模板**：用户明确要求 > 配置 `presentation` > 岗位推荐版式 > 标准版+商务蓝。确定后写入 `04-final.json` 的 `presentation`。

只在地区、企业或主题确有歧义时追问。只有用户要求“保存/以后默认”才写配置，并先备份旧版。真实配置、关注池和运行材料不进入公开技能包。

## 2. 校验配置、导入关注池与确定周期

`$SKILL` 为技能根目录，`$RUN` 为用户工作目录下本次的运行目录；输出只写入 `$RUN`。Windows可用 `py -3`；无Python时按契约人工核对并注明。

```bash
python3 "$SKILL/scripts/briefing_tool.py" validate-config "$RUN/config-effective.json"
python3 "$SKILL/scripts/briefing_tool.py" pool-template --out "$RUN/关注池模板.csv"      # 给用户一个可在Excel里填的模板
python3 "$SKILL/scripts/briefing_tool.py" pool-import --csv "$RUN/关注池.csv" --out "$RUN/关注池.json"
python3 "$SKILL/scripts/briefing_tool.py" window --kind daily   --as-of 2026-10-15T07:00:00+08:00
python3 "$SKILL/scripts/briefing_tool.py" window --kind weekly  --as-of 2026-10-15T07:00:00+08:00 [--period 2026-W42]
python3 "$SKILL/scripts/briefing_tool.py" window --kind monthly --as-of 2026-10-15T07:00:00+08:00 [--period 2026-09]
python3 "$SKILL/scripts/briefing_tool.py" window --kind special --as-of 2026-10-15T07:00:00+08:00 --period 2026-07-01/2026-09-30
python3 "$SKILL/scripts/briefing_tool.py" options    # 岗位视角、关注池类型、设区市、版式与配色
```

`pool-import` 接受中英文表头（名称、类型必填；别名、标签用分号分隔；优先级填高/中/低），出错时指出CSV行号。导入即表示用户确认这些条目可公开检索；合并进配置的 `watch_pool` 前给用户看一遍。

周期规则同父技能：晨报昨日、周报上一完整ISO周、月报上一完整自然月、专题自定（≤366天）；“本周/本月至今”出部分周期观察稿。周月报优先聚合已有晨报证据；用户点名历史周期而本地无存档时视为补采授权（`backfill_public`），首屏注明。详见[运行与证据契约](references/workflow.md)。

## 3. 采集与证据筛选

按[江西信源地图](references/source-map.md)建立省、设区市、县区和开发区的渠道清单，再按[信源与安全规范](references/sources-safety.md)使用宿主的公开搜索和原文读取能力；连接器可选（[可选连接器](references/optional-connectors.md)）。脚本不采集新闻。

1. 核实地区、关注池企业全称、别名和子公司范围，保留公开身份依据。
2. 建立“地区×政策通知/产业项目/企业公告/公开同业”覆盖矩阵，再为**每个高优先级关注池条目**单独检索一次；不用宽泛查询冒充全部渠道已查。
3. 保存原始URL、原始披露主体、发布日期、事件日、取得时间、摘录、来源根和核验状态。
4. 筛选：R=命中地区，P=命中关注池任一条目，T=命中 `topics`；`any` 模式为R或P或T，`all` 模式为R且P且T（空列表不参与），再叠加 `source_types` 与 `require_topic_match`。命中关注池的事项在 `pool_ids` 记录条目ID。
5. 按原始披露主体标 `publisher_type`；同根转载合并为一个事项。
6. 逐条填一个主 `theme` 和准确 `stage`；按岗位视角与 `focus_directions` 做编辑排序，`max_items` 只限制展示。
7. 停止前核对覆盖矩阵、高优先级关注池条目和高相关候选的处置；未完成就出观察稿并说明。

`primary_verified` 要求实际读到权威一手原文；未核实事项不进事实卡。处罚、违约等负面事项只认监管、司法、交易所或企业正式披露。拟受理、拟批准、审批决定公开、采购启动、废标、备案、投产分别表述；不同金额口径不得相加。**不从公开信息推断贷款需求、授信额度、营销机会或客户归属。**

## 4. 编制内容

先读[写作规范](references/writing-style.md)。晨报、周报、月报、专题的写法同父技能（见[周期分析](references/periodic-analysis.md)）。银行版额外要求：

- **一句话速览**：60–140字，优先写与 `focus_directions` 和高优先级关注池相关的实际变化；不写条数。
- **研究关注**（`reader_analysis`）：回答 `key_questions` 中能用公开信息回答的部分，写下一步可公开核实的节点；不写营销切入、拜访安排、授信建议或额度判断。
- **关注池**：最终报告写入 `focus`（岗位名称与关注方向）和 `pool`（关注池快照：id、名称、类型、优先级），事项写 `pool_ids`。页面自动生成“关注池动态”：本期命中的条目及其事项链接，以及**高优先级但本期未见已核实公开动态**的条目（只列名称，不推断原因）。关注池的标签与备注不进报告。
- **关键数字** `key_figures`、**后续关注** `watchlist`：同父技能，必须写口径并引用证据。

## 5. 质量检查

```bash
python3 "$SKILL/scripts/briefing_tool.py" lint --data "$RUN/04-final.json"
```

渲染前运行。`lint` 检查条数式摘要、读者正文夹带编制说明、推断授信与营销、投资建议、渲染性措辞、分类缺失、主题过碎等，输出质量分；有 `error` 必须改正。它不判断真实性与语义，通过后仍需人工复核。

## 6. 渲染

```bash
python3 "$SKILL/scripts/briefing_tool.py" render --data "$RUN/04-final.json" --out "$RUN/简报.html"
```

版式与配色同父技能（标准版、速读版、要报版 × 商务蓝、暖纸深红、素墨、青绿，默认商务蓝），正式版式写在 `presentation` 中，命令行参数只作临时预览。详见[模板与图表指引](references/templates.md)。页面不含脚本、远程字体、导出按钮；岗位名称只出现在折叠的运行记录中。输出文件已存在时拒绝覆盖。

## 7. 按需导出PNG长图

只在用户要求时运行，需Playwright与本地Chromium/Chrome/Edge，不自动下载浏览器：

```bash
python3 "$SKILL/scripts/export_png.py" --data "$RUN/04-final.json" --out "$RUN/分享长图.png" [--layout compact] [--executable-path /path/to/chrome]
```

导出只授权本地生成，不授权发送。长图含关注池动态面板；分享前提醒用户确认关注池名称可以对外展示。

## 8. 留存与交付

运行目录留存 `config-effective.json`（只含配置对象）、`00-raw.json`、`01-evidence.json`、`02-selected.json`、`03-analysis.json`、`04-final.json`、`RUN-STATE.json`。交付前确认：`coverage.method` 与实际做法一致；摘要逐句有证据；金额与阶段口径正确；关注池命中记录准确；`lint` 无error；宽窄屏无溢出。交付HTML、证据JSON、已请求的PNG和覆盖缺口说明，报告附非授信/投资意见声明。对外发送、邮件、部署和定时仅按用户另行明确的指令执行。
