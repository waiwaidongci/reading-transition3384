# gb-0043 测试与截图

本目录只放测试与证据，不混入源码。

## 测试文件

| 文件 | 说明 |
|---|---|
| `test_split_oracle.py` | 切分算法独立 oracle：分组规则（整条路线、共享连续三章、间接相连、跨文集同路线）、与暴力枚举逐一比对、无解只报证明结果、超时续跑与直接求解结果一致、非法/异序进度被忽略。只依赖标准库 + pytest，不连数据库/Redis。 |
| `test_split_flow.py` | HTTP 全流程：受理冻结快照与并发补录隔离、重复执行不重复发布、超时待定→续跑完成、无解理由、跨文集共用路线同侧、权限与审计。默认用临时 SQLite 与内存假队列，不需要真实 MySQL/Redis。 |
| `split_ui_flow.js` | 真实 Playwright 浏览器流程：分析师新建多文集切分→等 worker→逐文集人数、核验项、逐会话表、下载 CSV 并独立复算跨侧泄漏→viewer 权限→390 宽无横向溢出，并保存截图。 |

## 运行

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt pytest httpx
cd backend
../.venv/bin/python -m pytest ../test-artifacts/gb-0043 -q
```

浏览器流程需要可访问的前后端（`docker compose up -d --build` 后默认 `http://localhost:18247`）：

```bash
cd test-artifacts/gb-0043
npm i playwright && npx playwright install chromium
BASE=http://localhost:18247 node split_ui_flow.js
```

## 截图

| 文件 | 说明 |
|---|---|
| `split-result-desktop.png` | 1280 宽：切分完成后的逐文集核对、核验项、逐会话明细。 |
| `split-runner.png` | 1280 宽：排队中 / worker 正在切分的状态页。 |
| `split-infeasible.png` | 已证实无解（单会话文集）的说明。 |
| `split-viewer.png` | 业务阅读者视角：无新建表单、可下载核验表。 |
| `split-mobile.png` | 390 宽响应布局，无横向溢出。 |
