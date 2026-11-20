# 杉页阅读路径接续册

会话内一阶转移计数、条件概率与相对全局基率提升。为杉页研究协作组的12位分析与业务人员提供从样本核对、独立worker计算到批准快照交付的完整工作流。

## 真实业务场景

匿名章节阅读会话→路径接续关联→编辑衔接建议；仅会话内相邻，禁止跨会话拼接，非因果。团队原先将 `reading-transition-202611-01.csv` 复制到多份人工表格，复核时再逐项对照。三个痛点是口径不可追溯、重复计算易错、业务说明与结果脱节。上游提供合成业务观察，系统承担研究和复核，下游业务人员读取批准快照。全部演示数据是固定随机种子生成的模拟数据。

## 业务模块

1. 业务总览：批次、样本、任务、切分与批准交付账册。
2. 阅读文集：创建/修改来源说明、查看样本、用真实业务字段追加记录；列表分页。
3. 分析配置：冻结来源、参数和全部样本，进入Redis队列。
4. 完整结果：真实算法摘要、专属诊断、全部CSV导出和可恢复失败态。
5. 会话切分：多个文集一起切成练习集/验收集，防泄漏分组、逐会话核验、超时续跑。
6. 编辑衔接建议：分析师草案→负责人批准→阅读者下载批准快照。
7. 审计：登录、来源修改、任务worker完成、切分受理/完成、业务说明批准等事件。

## 快速启动

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
# 可重复执行，不覆盖人工修改
docker compose exec -T backend python -m scripts.seed_demo
```

访问 [前端](http://localhost:18247)；[API文档](http://localhost:19247/docs)。数据库仅演示用途映射 `17747`。首次准备依赖和初始化数据库需要等待健康状态：`docker compose ps`，五个服务均需healthy。

| 组件 | 技术与职责 |
|---|---|
| frontend | 原生 HTML / CSS / JS ES Modules，Nginx代理 `/api/` |
| backend | Python3.11 / FastAPI / SQLAlchemy / JWT |
| worker | 独立Python进程，Redis BLPOP队列，持久化结果/CSV/心跳 |
| db | MySQL8，命名卷持久化 |
| redis | Redis7，AOF命名卷 |

恰好五服务，均配置healthcheck；后端/worker依赖数据库与Redis健康，前端依赖后端健康；worker与后端共享 `artifacts` 命名卷。重置演示环境：`docker compose down -v --remove-orphans`，会删除本项目卷。

## 账号与权限

所有演示账号密码：`Research2026!`。

| 邮箱 | 角色 | 权限 |
|---|---|---|
| admin@reading-transition.demo | admin | 全部录入/执行、批准业务说明 |
| analyst@reading-transition.demo | analyst | 录入来源/样本、执行、提交说明，无批准权 |
| viewer@reading-transition.demo | viewer | 只读、导出结果及已批准快照 |

时间在数据库/API中以UTC存储，总览与审计页面明确转换为Asia/Shanghai北京时间。

JWT有效期8小时；PBKDF2盐化哈希保存密码；角色每请求从数据库读取。访客写操作403，匿名访问业务数据401，重复样本409，非法业务字段422，重复批准409。每客户端/路径写请求90次/分钟，超限429。生产使用需修改JWT及演示数据库凭据。

## 实体与字段

ReadingCollection / ReadingSession / TransitionRun / SessionSplit / EditorialPlan 是核心实体；另有User和Audit。数据库实际表：`reading_collections` → `reading_sessions` → `transition_runs` → `session_splits` → `editorial_plans`。样本以固定字段JSON保存，后端按以下字段严格验证，不接受未知字段替代必填值：匿名会话编号（`session`）、章节路径（用 > 分隔）（`path`）。

实体贯穿 `backend/src/models`、`schemas`、`services`、`controllers`、`routes`，以及 `frontend/src/api/research.js`、`stores/research.js` 和 `pages`。`Source.key` 与 `(Record.source_id, reference)` 为自然键；来源改名不会修改已冻结任务输入。业务说明在创建时保存结果快照，批准后从该快照导出。

## 算法与口径

会话内一阶转移计数、条件概率与相对全局基率提升，全部计算位于 `backend/src/services/algorithm.py`，前端不拟合模型、不生成模拟结果。输入是冻结业务观察及参数，输出summary、专属诊断、逐行计算明细、seed与输入SHA256。CSV包含全部结果行；页面仅预览前30行。参数：

- `min_count`：最少接续次数，默认 `3`。
- `top_n`：保留接续规则数，默认 `20`。

只统计同一会话内的相邻章节；提升度相对于所有接续边的目标章节基率，关联不代表编辑调整造成的因果效果。

失败态：业务字段缺失/非有限数值、参数越界、样本不足、退化特征或结构错误均明确拒绝或将任务标failed，错误消息保存在任务中。可回到来源或配置修正后重新执行，历史失败记录保留。

已知边界：仅统计每行完整匿名会话内的相邻章节，拒绝会话编号重复；不是长序列规则挖掘。无跨会话拼接；提升度只是接续关联，不解释为因果。单章会话有效但不产生边。

## 会话切分与防泄漏

同一路线混进练习集和验收集会把验收分数抬高。切分任务在 `POST /splits` 受理时把所选文集的会话逐行复制成任务快照：之后来源改名、追加记录都不会改变本次切分，并发补录不混入。算法位于 `backend/src/services/split_algorithm.py`（纯函数，只用标准库），任务与续跑位于 `services/splits.py`，worker 从同一 Redis 队列按 `split:<id>` 前缀消费。

- **分组**：同一路线（章节序列完全相同）、共享连续三章（含间接相连，传递闭包）、同一会话编号的会话并入一组，组内会话只能同侧。
- **平衡**：每个所选文集在练习集与验收集都至少 1 人，且两侧人数最多差 1；单文集目标为 `floor(N/2)` 或 `ceil(N/2)`。
- **精确搜索**：只涉及一个文集的本地组用子集和位集判定，跨文集组用带回溯的深度优先枚举并以本地可达区间剪枝；两层都精确，所以“已证实无解”是穷尽或直接证明的结论，绝不因超时而误报。
- **超时待定**：搜索超出 `budget_seconds` 时任务进入 `pending` 并保存搜索栈；`POST /splits/:id/resume` 从该进度续跑，不从头重算。worker 长时间无进展的 `running` 任务也可续跑。
- **不重复发布**：worker 以条件更新领取 `queued` 任务，重复投递或重复重试只会处理一次。
- **逐会话核验**：结果包含每会话的分组、所属侧、文集与章节路径，并再次核验覆盖、组内同侧、两侧人数差≤1、两侧都有人、无重复路线/连续三章/会话编号跨侧；任一失败即拒绝交付。CSV 含全部行，页面预览前 30 行。

访客（viewer）可查看切分与下载逐会话 CSV，不能新建或续跑（403）。

## 演示数据与一天剧本

固定seed=42，3个来源 ` SY-202611-01/02/03`，每来源240条，共720条业务记录；三个账号和首轮种子任务。每个文集的章节路线由固定词表与固定洗牌生成（3 条路线被三个文集共用，用来演示跨文集分组），也用同一份路线为会话编号也作为分组依据。自然键检查后仅插入缺失数据，连跑seed不重复、不覆盖用户修改。`SEED_DEMO=true` 时启动自动执行；失败记录日志但不阻塞服务。首轮任务同样由独立worker真实计算。

1. 09:00 负责人查看三份来源和720条样本。
2. 09:10 分析师选择 `SY-202611-01 秋季常规组`，核对 `reading-transition-202611-01.csv`。
3. 09:15 在“阅读文集”核对匿名会话编号（`session`）、章节路径（用 > 分隔）（`path`），追加编号 `SY-NEW-001` 的样本。
4. 09:20 新建“十一月复核研究”，按默认参数冻结241条输入。
5. 09:21 独立worker消费Redis队列，返回实算结果与输入校验和。
6. 09:25 分析师查看专属诊断及逐行结果，并下载CSV复算。
7. 09:30 提交“十一月业务复核意见”，负责人核对并批准。
8. 09:40 viewer下载批准快照，查看审计中的worker完成与批准事件。
9. 09:45 分析师在“会话切分”勾选三个文集，受理后冻结720条会话；worker返回练习集/验收集各360条、46组，逐会话核验全部通过。
10. 09:50 示范超时待定与续跑：调小搜索预算，任务保留进度，续跑后得到同一份切分；若某文集全部会话同组，则直接报告已证实无解。

## 目录与本地开发

```text
frontend/src/api, stores, types, components/common, hooks, pages, router, utils, constants
backend/src/routes, controllers, services, models, schemas, middlewares, utils, constants, config
backend/scripts/seed_demo.py
backend/migrations/001_initial.sql
```

本地后端：Python3.11虚拟环境安装 `backend/requirements.txt`，设置 `DATABASE_URL`、`REDIS_URL`、`ARTIFACT_DIR`，在backend目录运行 `uvicorn src.main:app --reload`；另开终端运行 `python -m src.worker`。原生前端无需编译，推荐以Compose Nginx访问，保证 `/api/` 代理。前端为Google Fonts双字体、oklch色板，离线字体回退serif；1280/768/390响应布局。

| 环境变量 | 默认与说明 |
|---|---|
| COMPOSE_PROJECT_NAME | `reading-transition`，隔离容器/网络/命名卷 |
| FRONTEND_PORT / BACKEND_PORT / DB_PORT | `18247 / 19247 / 17747` |
| SEED_DEMO | `true` |
| JWT_SECRET | `.env.example`演示值，部署前修改 |
| DATABASE_URL / REDIS_URL | Compose内部连接，定义在compose的env锚点 |
| ARTIFACT_DIR | `/artifacts`，后端worker共享 |

共享枚举：`backend/src/constants/enums.py` 与 `frontend/src/constants/enums.js` 都定义 admin/analyst/viewer、queued/running/completed/failed、切分的 queued/running/pending/completed/infeasible/failed、draft/approved。后端service检查对应字符串；README为全量值说明。共享前端组件table/status/chart/business跨总览、结果、切分、配置与复核复用；actions hook和format utils负责交互/格式。后端errors与rate_limit是两个中间件。

测试及截图在批根 `test-artifacts/gb-0043/`，不混入源码。独立算法oracle、HTTP完整流程和真实Playwright浏览器流程见该目录 `TESTING.md`。License：MIT。
