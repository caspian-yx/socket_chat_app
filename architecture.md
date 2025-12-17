
# Socket 项目目录结构草案

## 1. 根目录结构
```
Socket/
├── client/                     # 客户端源码与资源
│   ├── core/                   # 网络层、协议封装、会话管理
│   ├── features/               # 聊天、文件、语音等业务模块
│   ├── ui/                     # CLI/UI 层与交互
│   ├── storage/                # 本地缓存与消息历史
│   └── main.py                 # 客户端入口
├── server/                     # 服务器端源码
│   ├── core/                   # 连接管理、协议解析、任务调度
│   ├── services/               # 用户、消息、文件、加密等服务
│   ├── storage/                # 数据访问层（数据库/文件）
│   ├── workers/                # 后台任务（离线消息推送、断点续传等）
│   └── main.py                 # 服务器入口
├── shared/                     # 公共组件与协议定义
│   ├── protocol/               # 协议数据结构、编码解码
│   ├── utils/                  # 工具库：加密、序列化、日志等
│   └── settings.py             # 全局配置（端口、数据库、路径）
├── docs/                       # 设计与说明文档
│   ├── architecture.md         # 架构草案（本文件）
│   ├── protocol.md             # 消息协议草案
│   └── README.md               # 文档索引与进度
├── scripts/                    # 部署、测试、构建脚本
├── tests/                      # 单元测试与集成测试
├── requirements.txt            # Python 依赖
├── pyproject.toml / setup.cfg  # 可选的打包配置
└── README.md                   # 项目说明
```

## 2. 模块职责概览

- `client/core`: 客户端网络通信的底层实现，维护与服务器的长连接、心跳、重连。
- `client/features`: 以功能维度组织业务模块，示例：
  - `messaging`: 点对点聊天、群聊逻辑。
  - `presence`: 在线状态管理。
  - `file_transfer`: 文件上传/下载、断点续传。
  - `voice`: 后续扩展的语音功能。
- `client/ui`: 提供命令行或图形界面，与 core/feature 层解耦。
- `client/storage`: 本地持久化（SQLite/JSON）消息历史、断点信息、配置。
- `server/core`: 负责监听端口、管理连接、任务调度（asyncio 或多线程）、协议解析和安全校验。
- `server/services`: 业务服务组件：
  - `auth_service`: 用户认证、会话管理、Token。
  - `presence_service`: 在线状态维护、心跳检测。
  - `message_router`: 消息分发、离线消息队列。
  - `file_service`: 文件传输、断点续传元数据。
  - `crypto_service`: 加密/解密策略。
- `server/storage`: 数据访问层，封装 SQLite/SQL Server 操作；定义 ORM/DAO（如 SQLAlchemy）。
- `server/workers`: 异步任务、计划任务（如离线消息推送、日志归档）。
- `shared/protocol`: 定义协议常量、消息模型、序列化与校验逻辑（JSON Schema、Pydantic）。
- `shared/utils`: 公共工具，如日志封装、时间处理、id 生成、加密包装。
- `shared/settings.py`: 集中管理端口、数据库配置、密钥等，可读取 `.env`。
- `scripts`: 集成测试、部署工具、数据迁移脚本。
- `tests`: 全局测试目录，`client` 与 `server` 子目录可分别拥有单元/集成测试。

## 3. 技术栈建议

- Python 版本：3.10+
- 服务端框架：`asyncio` + `asyncio.Streams` / `asyncio.start_server`（后续可选 `asyncio` + `uvloop` 提升性能）。
- 序列化：JSON（文本消息） + 二进制帧（文件、语音）。
- 数据库：初期 `SQLite`（`aiosqlite`），后续可替换为 `SQL Server`。
- 日志：`structlog` 或标准库 `logging`，结合 JSON 输出。
- 测试：`pytest` + `pytest-asyncio`。

