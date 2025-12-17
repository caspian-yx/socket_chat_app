# 模块与函数说明（introduction.md）

本文聚焦仓库中三个主要分层（shared / client / server），说明各文件夹、核心类与函数的作用及调用关系，便于后续扩展或代码走读。

---

## 1. Shared 层（共享协议与工具）

| 模块 | 关键类型 / 函数 | 作用 |
| ---- | --------------- | ---- |
| `shared/protocol/commands.py` | `MsgType`、`normalize_command`、`is_command` | 定义所有协议命令字串（auth/presence/message/file），并提供命令判断或归类函数，供 client/server 统一使用。 |
| `shared/protocol/constants.py` | `DEFAULT_VERSION`、`FRAME_DELIMITER`、`MAX_PAYLOAD_SIZE` | 定义协议常量（版本号、分帧符、控制通道最大载荷等）。 |
| `shared/protocol/errors.py` | `StatusCode`、`ErrorCode`、`ProtocolError` | 统一状态码/业务错误码；`ProtocolError.to_payload()` 用于构建响应 `payload`。 |
| `shared/protocol/framing.py` | `encode_msg`、`decode_msg`、`async_decode_msg`、`encode_chunk`、`decode_chunk` | JSON + `\n` 分帧的编解码；同时提供 TLV 二进制块编码，为文件通道铺路。 |
| `shared/protocol/messages.py` | `BaseMsg`、`LoginMsg`、`AuthAckMsg`、`HeartbeatMsg`、`MessageSendMsg` 等 | 基于 Pydantic 的数据模型，`from_dict` 方法会在结构不符时抛出 `ProtocolError`。 |
| `shared/protocol/validator.py` | `load_schema`、`validate_msg`、`validate_version`、`validate_signature` | 加载 `schemas/*.json` 进行 JSON Schema 校验，验证版本/签名，集中处理协议一致性。 |
| `shared/settings.py` | `Settings` dataclass、`load_settings` | 用于共享基础配置（端口、数据目录、密钥），client 与 server 可按需扩展。 |
| `shared/utils/common.py` | `generate_message_id`、`utc_timestamp`、`sha256_hex`、`random_token` | 常用工具（ID、时间戳、哈希、随机 token）。 |

> **共享调用方式**：`from shared.protocol import MsgType, encode_msg, validate_msg` 等，`__all__` 已在 `shared/protocol/__init__.py` 中暴露。

---

## 2. Client 层

### 2.1 配置与入口

- `client/config.py`  
  - `load_config()` 读取 `.env` + 环境变量（`CLIENT_*` 前缀），合并到 `CLIENT_CONFIG`。  
  - `_coerce_type` / `_validate_config` 保证端口、心跳、token 过期等参数合法。
- `client/main.py`  
  - `run_client()`：顺序加载配置 → 初始化 `NetworkClient`、`ClientSession`、`LocalDatabase`、特性管理器 → 启动 CLI。  
  - 通过 `asyncio.run` 执行。

### 2.2 核心模块

- `client/core/network.py`  
  - `NetworkClient.connect()`：带指数退避的重连策略，成功后起 `_receive_loop` 与 `_heartbeat_loop`。  
  - `send()`：发送前调用 `validator.validate_msg`，失败抛 `NetworkError`。  
  - `register_handler()` / `_dispatch()`：features 可注册命令对应的 async handler。  
  - `open_file_channel()`：文件/音频扩展使用的独立 TCP 通道。
- `client/core/session.py`  
  - `build_headers()` / `attach_headers()`：为消息自动注入 `version`、`Authorization` 等。  
  - `set_authenticated()`：使用 `AuthAckMsg.from_dict` 验证 login/refresh 响应，并更新 token 状态。  
  - `refresh_token()`：发送 `auth/refresh` 请求，实际的 ACK 由 `AuthManager` 回调。

### 2.3 业务功能

- `client/features/auth.py` (`AuthManager`)  
  - 负责 login/logout/refresh 三个流程。  
  - `_handle_login_ack`/`_handle_refresh_ack` 注册到 `NetworkClient`；内部通过 `asyncio.Event` 同步 CLI。
- `client/features/messaging.py` (`MessagingManager`)  
  - `send_text()` 构造 `MessageSendMsg`，发送后落地到 `LocalDatabase.save_outbound_message`。  
  - `_handle_event()`：接收 `message/event`，推入 `asyncio.Queue` 并保存到本地。  
  - `next_message()`：供 UI 或其他协程消费消息。
- `client/features/presence.py` (`PresenceManager`)  
  - `request_roster()` 发送 `presence/list` 并等待 `_presence_event`。  
  - `_handle_event/_handle_list_response` 更新最新在线列表。
- `client/features/file_transfer.py` (`FileTransferManager`)  
  - `send_file()` 示例性地使用 `open_file_channel` + `encode_chunk` 发送二进制；接收逻辑待实现。

### 2.4 存储与 UI

- `client/storage/local_db.py` (`LocalDatabase`)  
  - 初始化 SQLite `messages` 表；`save_outbound_message`/`save_inbound_message` 写入消息；`recent_messages()` 查询历史。  
- `client/storage/cache.py` (`InMemoryCache`)  
  - 简单的 TTL 键值缓存，用于 presence 或临时数据。
- `client/ui/cli.py` (`ChatCLI`)  
  - 通过 `input()` 读取命令，调用 `AuthManager` / `MessagingManager` / `PresenceManager` 执行业务。  
  - `send`/`login`/`presence`/`quit` 四个指令。

---

## 3. Server 层

### 3.1 核心骨架

- `server/config.py`：`load_server_config()` 读取 `SERVER_*` 环境变量。  
- `server/core/connection.py` (`ConnectionContext`)：保存 reader/writer、peer、user_id、token、心跳时间。  
- `server/core/router.py` (`CommandRouter`)：`register()` 注册命令 → handler；`dispatch()` 被服务器调用。  
- `server/core/server.py` (`SocketServer`)  
  - `start()`：初始化 `asyncio.start_server`。  
  - `_handle_client()`：循环读取分帧 → `validator.validate_msg` → `CommandRouter.dispatch` → `_send()`。  
  - `_error_response()`：将 `ProtocolError` 映射为统一响应。

### 3.2 服务层

- `server/services/auth_service.py` (`AuthService`)  
  - `handle_login()`：校验账号、生成 token，调用 `ctx.mark_authenticated()`。  
  - `handle_logout()`：删除 session，重置 `ConnectionContext`。  
  - `handle_refresh()`：换发新 token，返回 `auth/refresh_ack`。  
  - 自动补齐响应 headers 的 `version`。
- `server/services/presence_service.py` (`PresenceService`)  
  - `handle_update()`：验证状态并写入仓储；未登录用户会收到 401。  
  - `handle_list()`：返回在线用户数组。  
  - `broadcast_event()`：构建 `presence/event`（供未来推送使用）。
- `server/services/message_service.py` (`MessageService`)  
  - `handle_send()`：保存消息并返回 `message/ack`（附 message_id）。

### 3.3 存储与后台

- `server/storage/memory.py` (`InMemoryRepository`)  
  - 预置用户 `alice/bob`，SHA256 存储密码。  
  - `store_session/delete_session`、`update_presence/list_online_users`、`store_message` 等方法。  
- `server/workers/offline.py` (`OfflineDispatcher`)  
  - `start()` 创建后台任务，`_run()` 周期打印心跳（展示 worker 接入方式）。

### 3.4 服务器入口

- `server/main.py`  
  - 初始化仓储与服务对象 → 注册路由（auth/login, auth/logout, auth/refresh, presence/update, presence/list, message/send） → `SocketServer.start()` → `await asyncio.Event().wait()` 常驻。

---

## 4. 典型调用链

### 4.1 登录流程

1. CLI 调用 `AuthManager.login()` → 构造 `LoginMsg` → `NetworkClient.send()`。  
2. Server `SocketServer` 收到报文 → `CommandRouter` 调用 `AuthService.handle_login()`。  
3. `AuthService` 校验凭证、生成 token → 响应 `auth/login_ack`。  
4. 客户端 `_receive_loop` 调用 `_handle_login_ack()` → `ClientSession.set_authenticated()` → CLI 得到事件通知。

### 4.2 文本消息发送

1. `MessagingManager.send_text()` 构造 `MessageSendMsg`， session 注入 headers。  
2. Server `MessageService.handle_send()` 写入 `InMemoryRepository.store_message()` 并返回 `message/ack`。  
3. `_handle_ack()` 可根据需要更新 UI / 重发策略；消息本地持久在 `LocalDatabase`。

### 4.3 在线列表查询

1. `PresenceManager.request_roster()` 发出 `presence/list`，等待 `_presence_event`。  
2. Server `PresenceService.handle_list()` 从仓储读取在线用户返回。  
3. 客户端 `_handle_list_response()` 更新 `_latest_roster`，CLI 输出在线用户。

---

## 5. 扩展指引

- 若要新增命令：在 `shared/protocol/commands.py` 定义枚举 → 添加 JSON Schema → 编写模型/handler。  
- 若要增加新的 feature：在 `client/features` 创建管理器，`NetworkClient.register_handler()` 注册回调。  
- 服务端扩展时，推荐新增 Service + 在 `server/main.py` 中注册路由；仓储层可替换为数据库实现。  
- 文件/语音：利用 `Framing.encode_chunk` 与 `NetworkClient.open_file_channel` 建立二进制通道。

通过本文可快速理解每个模块的职责及函数调用顺序，支持后续对协议升级、业务拓展或性能调优。
