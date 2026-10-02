# Personal-Growth-Agent

个人成长助手后端 API。基于 FastAPI + MySQL + SQLAlchemy，支持用户注册登录、成长计划管理、打卡记录与统计、AI 目标拆解。

## 技术栈

| 层级       | 技术                                        |
| ---------- | ------------------------------------------- |
| Web 框架   | FastAPI 0.138                               |
| ORM        | SQLAlchemy 2.0                              |
| 数据库     | MySQL 8 + PyMySQL                           |
| 迁移       | Alembic                                     |
| 认证       | JWT (python-jose) + OAuth2 Password Bearer  |
| 密码哈希   | PBKDF2-HMAC-SHA256 (100k 迭代)              |
| LLM        | teamorouter 中转 (glm-5.3-flash)            |
| 校验       | Pydantic 2                                  |
| Agent 编排 | LangGraph（StateGraph + 条件边 + 循环校验） |
| 前端       | Streamlit（表单 + session_state）           |
| 本地大模型 | Ollama（qwen3:8b）                          |

## 目录结构

```
Person_Growth_Agent/
├── app/
│   ├── agents/                  # LangGraph 目标拆解 Agent
│   │   ├── goal_breakdown.py    # 图定义：parse_goal→闸门→周/日计划→校验循环→存库
│   │   └── app.py               # Streamlit 前端
│   └── tests/                   # pytest 测试（纯函数 + 集成）
│       ├── test_goal_parser.py
│       ├── test_agent_nodes.py
│       └── test_e2e.py
│   ├── main.py                  # 应用入口，挂载路由 + 全局异常
│   ├── database.py              # 数据库连接 / Session
│   ├── deps.py                  # 公共依赖（get_current_user 鉴权）
│   ├── core/
│   │   ├── config.py            # 配置（.env 读取）
│   │   ├── security.py          # 密码哈希 + JWT 签发/验证
│   │   └── exceptions.py        # 自定义异常体系 + 全局处理器
│   ├── models/                  # SQLAlchemy 模型
│   │   ├── user.py
│   │   ├── plan.py
│   │   ├── checkin.py
│   │   └── memory.py            # 预留，未启用
│   ├── schemas/                 # Pydantic 请求/响应模型
│   │   ├── user.py
│   │   ├── plan.py
│   │   └── checkin.py
│   ├── crud/                    # 数据库操作层
│   │   ├── user.py
│   │   ├── plan.py
│   │   └── checkin.py
│   ├── routers/                 # API 路由层
│   │   ├── auth.py              # 注册 / 登录
│   │   ├── users.py             # /users/me
│   │   ├── plans.py             # 计划 CRUD + AI 拆解
│   │   └── checkins.py          # 打卡 CRUD + 统计
│   └── llm/
│       └── client.py            # LLM 调用封装（重试/超时/限流）
├── alembic/                     # 数据库迁移
├── .env                         # 环境变量（不提交到 git）
├── requirements.txt
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

创建 `.env` 文件：

```env
DATABASE_URL=mysql+pymysql://root:password@localhost:3306/Person_Growth_Agent
SECRET_KEY=your-secret-key-hex
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.teamorouter.cn
LLM_MODEL=glm-5.3-flash
LLM_TIMEOUT=60
LLM_MAX_RETRIES=3
```

### 3. 初始化数据库

```bash
alembic upgrade head
```

### 4. 启动服务

```bash
uvicorn app.main:app --reload
```

启动后访问 `http://localhost:8000/docs` 查看交互式 API 文档。

## API 一览

### 认证 `/auth`

| 方法 | 路径             | 说明                                              |
| ---- | ---------------- | ------------------------------------------------- |
| POST | `/auth/register` | 注册（body: name, password）                      |
| POST | `/auth/login`    | 登录（OAuth2 表单: username, password）→ 返回 JWT |

### 用户 `/users`

| 方法 | 路径        | 说明                                    |
| ---- | ----------- | --------------------------------------- |
| GET  | `/users/me` | 获取当前登录用户信息（需 Bearer Token） |

### 计划 `/plans`（全部需登录）

| 方法   | 路径                          | 说明                     |
| ------ | ----------------------------- | ------------------------ |
| POST   | `/plans`                      | 创建计划                 |
| GET    | `/plans?skip=&limit=&status=` | 分页列表 + 状态筛选      |
| GET    | `/plans/{id}`                 | 计划详情                 |
| PUT    | `/plans/{id}`                 | 更新计划（部分字段）     |
| DELETE | `/plans/{id}`                 | 删除计划（级联删除打卡） |
| POST   | `/plans/ai-decompose`         | AI 目标拆解为按周计划    |

### 打卡 `/checkins`（全部需登录）

| 方法 | 路径                                                    | 说明                                 |
| ---- | ------------------------------------------------------- | ------------------------------------ |
| POST | `/checkins`                                             | 创建打卡（同天同计划同类型不可重复） |
| GET  | `/checkins?plan_id=&start_date=&end_date=&skip=&limit=` | 分页列表 + 筛选                      |
| GET  | `/checkins/{id}`                                        | 打卡详情                             |
| GET  | `/checkins/stats/plan/{plan_id}`                        | 按计划统计（次数/时长/强度/完成率）  |

## 数据库表结构

### user
| 字段                      | 类型               | 说明                       |
| ------------------------- | ------------------ | -------------------------- |
| id                        | INT PK             | 用户 ID                    |
| name                      | VARCHAR(50) UNIQUE | 用户名                     |
| password_hash             | VARCHAR(100)       | 密码哈希（salt$hash 格式） |
| preference                | TEXT               | 用户偏好（预留）           |
| create_time / update_time | DATETIME           | 时间戳                     |

### plan
| 字段                   | 类型         | 说明                                             |
| ---------------------- | ------------ | ------------------------------------------------ |
| id                     | INT PK       | 计划 ID                                          |
| user_id                | INT FK→user  | 所属用户                                         |
| goal                   | VARCHAR(255) | 目标                                             |
| daily_plan / week_plan | TEXT         | 日计划 / 周计划                                  |
| start_time / end_time  | DATETIME     | 计划周期                                         |
| stauts                 | VARCHAR(50)  | 状态（字段名拼写遗留，对外 API 暴露为 `status`） |

### checkin
| 字段         | 类型        | 说明                  |
| ------------ | ----------- | --------------------- |
| id           | INT PK      | 打卡 ID               |
| plan_id      | INT FK→plan | 所属计划              |
| user_id      | INT FK→user | 所属用户              |
| checkin_date | DATE        | 打卡日期              |
| task_type    | VARCHAR(50) | 类型（学习/运动/...） |
| duration     | FLOAT       | 时长（小时）          |
| intensity    | INT         | 强度（1-5）           |

## 错误响应格式

所有业务错误统一返回：

```json
{
  "error_code": "NOT_FOUND",
  "message": "计划不存在或无权访问",
  "path": "/plans/123"
}
```

| HTTP 状态 | error_code              | 场景                               |
| --------- | ----------------------- | ---------------------------------- |
| 401       | UNAUTHORIZED            | 登录失败 / token 无效 / 用户不存在 |
| 404       | NOT_FOUND               | 资源不存在或无权访问               |
| 409       | CONFLICT                | 用户名重复 / 重复打卡              |
| 429       | LLM_RATE_LIMITED        | AI 服务限流                        |
| 502       | LLM_SERVICE_UNAVAILABLE | AI 服务不可用                      |

> 越权访问统一返回 404（不泄露资源是否存在）。

## 设计要点

- **分层架构**：routers → crud → models，schema 做请求/响应校验
- **数据隔离**：所有查询带 `user_id` 过滤，越权返回 404
- **重复打卡**：同一天 + 同一计划 + 同一 task_type 唯一
- **完成率**：打卡天数 / 计划总天数 × 100%
- **级联删除**：删除计划时自动删除其下所有打卡记录
- **异常统一**：自定义异常基类 + 全局 handler，错误格式一致
### 5. 启动目标拆解 Agent 前端

确保本地 Ollama 已拉取模型：

```bash
ollama pull qwen3:8b
ollama serve
```