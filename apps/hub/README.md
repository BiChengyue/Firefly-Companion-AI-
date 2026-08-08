# Hub（personal-control-hub 迁入）

> D-3 迁入同仓（2026-08-06）：原 `E:\Reasonix\workplace\personal-control-hub\src\hub\` 迁入本目录，
> **保持独立进程 hub-api**，三库 hub_state / hub_private / hub_audit 保留，与 companion 后端不混跑。
> 本机源目录 `E:\Reasonix\workplace\personal-control-hub\` 保留待切换完成后退役；服务器生产副本不动。

## 目录

```
apps/hub/
  main.py           hub-api 独立进程入口
  src/hub/          Hub 核心（api_server / ingress / storage / contracts /
                    state_engine / context_gateway / projects / simulator / sr_account）
  contracts/        JSON Schema（event / context / intent / action / error-codes）
  tests/            pytest（60 passed 门禁）
  requirements.txt        运行依赖（jsonschema / psutil）
  requirements-dev.txt    测试依赖（含运行依赖 + pytest）
  .venv/            独立虚拟环境（同仓两进程形态：Hub 不共用 server venv）
```

## 依赖与安装

```bash
cd apps/hub
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # 含运行 + 测试依赖
```

运行依赖：`jsonschema>=4.23`（契约校验）、`psutil>=5.9`（server-status）。
传递依赖（attrs / referencing / rpds-py）由 pip 自动解析安装，无需手工声明。

## 运行

```bash
cd apps/hub
.venv/Scripts/python -m pytest               # 60 passed
.venv/Scripts/python main.py                 # 启动 hub-api（等价 python -m src.hub.api_server）
```

环境变量（与原 Hub 一致）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PCH_TOKEN` | 令牌文件 `C:\ProgramData\firefly-bot\pch.token` | 只读接口鉴权 |
| `PCH_PHONE_TOKEN` | — | 检测器/手机上报鉴权 |
| `PCH_DATA_DIR` | `~/pch-data` | 三库数据目录 |
| `PCH_PORT` | `8901` | 监听端口 |
| `PCH_BIND` | `0.0.0.0` | 监听地址 |
| `PCH_WATCHED_SERVICES` | `firefly-qbot` | server-status 探测服务列表 |
| `PCH_PROJECTS_DIR` | `C:\ProgramData\firefly-bot\projects` | 项目接管文件目录 |
| `SR_ACCOUNT_FILE` | `C:\ProgramData\firefly-bot\data\sr_account.json` | 星铁账号数据 |

## 只读/归档接口（HTTP）

鉴权：只读接口 `X-PCH-Token`（`PCH_TOKEN` 或令牌文件）；检测器/手机上报 `X-Phone-Token`（`PCH_PHONE_TOKEN`）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/status` | 三库健康/统计 |
| GET | `/api/v1/events?after=<ts>` | 未消费主动事件 |
| POST | `/api/v1/events/consumed` | 消费确认（X-Phone-Token） |
| GET | `/api/v1/context?project=<name>` | 脱敏项目上下文 |
| GET | `/api/v1/fitness-state` | 最新健康缓存（fitness_sync 链路） |
| GET | `/api/v1/fitness/history?days=N` | 近 N 天健康历史（T31；默认 7、上限 90） |
| GET | `/api/v1/phone-location` | 手机定位缓存 |
| POST | `/api/v1/ingest/event` | 手机事件上报（X-Phone-Token） |
| POST | `/api/v1/ingest/computer` | 电脑状态上报 |
| POST | `/api/v1/ingest/fitness` | 健康缓存更新（fitness_sync，X-PCH-Token） |
| POST | `/api/v1/ingest/fitness-history` | 健康历史归档，date 主键 upsert 幂等（T31；body `{"dates":[{date,steps,sleep:{secs,score},resting_hr,spo2,vo2max,weight}]}` 或裸数组） |

## 与总线的衔接（CONTRACTS §0 架构图）

- 总线（`apps/server/app/core/bus/`）负责输入路由、输出登记与生成；Hub 派发器按去处序列逐级投递。
- 本迁入保留 Hub 全部既有能力（三库、鉴权、脱敏上下文、设备签名/重放防护、只读接口），
  消息总线对 Hub 的接入（hub_event 入站）由 D 包后续工单接线。

## 边界

- 生产副本（服务器）保持运行，重构验收通过后才切换。
- 不部署、不重启 NSSM 服务；本目录仅本地开发与测试。
