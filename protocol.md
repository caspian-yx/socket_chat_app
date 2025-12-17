# Socket 消息协议草案

## 1. 报文结构

所有控制与文本消息使用 JSON 编码，通过 TCP 长连接进行传输。统一的报文骨架如下：

```json
{
  "id": "uuid",             // 全局唯一 ID，追踪请求/响应链路
  "type": "request",        // request / response / event
  "timestamp": 1730795173,  // Unix 时间戳（秒）
  "command": "auth/login",  // 命令名称，斜杠分层
  "headers": {              // 可选元信息：trace、版本、客户端信息等
    "version": "1.0",
    "client": "cli-0.1.0"
  },
  "payload": {              // 业务数据，因命令而异
    "...": "..."
  }
}
```

### 1.1 编码约定
- 所有 JSON 字段使用 UTF-8 编码传输，末尾追加 `\n` 分帧。
- 服务端使用 `shared/protocol/framing.py` 封装编解码，防止黏包和拆包问题。
- 对于大文件/音频流，采用单独的数据通道或二进制帧，详见第 5 节。

### 1.2 状态码
响应报文在 `payload.status` 返回状态码，约定与 HTTP 语义类似：

| 状态码 | 含义               |
| ------ | ------------------ |
| 200    | 成功               |
| 202    | 已接受（异步处理） |
| 400    | 请求参数错误       |
| 401    | 未认证/凭证失败    |
| 403    | 禁止访问           |
| 404    | 资源不存在         |
| 409    | 资源冲突（如会话已存在） |
| 429    | 频率限制           |
| 500    | 服务器内部错误     |

## 2. 核心命令定义

### 2.1 身份认证

| 命令           | 方向         | 描述               |
| -------------- | ------------ | ------------------ |
| `auth/login`   | Client → Server | 用户登录，建立会话 |
| `auth/logout`  | Client → Server | 主动登出           |
| `auth/refresh` | Client → Server | 刷新会话令牌       |
| `auth/kick`    | Server → Client | 强制下线通知       |

#### 请求示例：`auth/login`
```json
{
  "id": "9fbb4e7e-73f6-4e71-9f44-70cdaeb71b0b",
  "type": "request",
  "timestamp": 1730795173,
  "command": "auth/login",
  "payload": {
    "username": "alice",
    "password": "hashed-password",
    "client_info": {
      "device": "windows",
      "version": "cli-0.1.0"
    }
  }
}
```

#### 响应示例
```json
{
  "id": "9fbb4e7e-73f6-4e71-9f44-70cdaeb71b0b",
  "type": "response",
  "timestamp": 1730795174,
  "command": "auth/login",
  "payload": {
    "status": 200,
    "token": "jwt-or-session-id",
    "user_id": "u-10001",
    "expires_in": 3600
  }
}
```

### 2.2 在线状态与心跳

| 命令                | 方向            | 描述                              |
| ------------------- | --------------- | --------------------------------- |
| `presence/heartbeat`| 双向            | 心跳包，维持在线状态              |
| `presence/update`   | Client → Server | 客户端状态变更（online/away/busy）|
| `presence/list`     | Client → Server | 拉取在线用户列表                  |
| `presence/event`    | Server → Client | 在线状态变更推送                  |

心跳包可采用轻量 payload：
```json
{
  "id": "hb-1730795200",
  "type": "event",
  "command": "presence/heartbeat",
  "payload": {
    "seq": 1024
  }
}
```

### 2.3 消息通信

| 命令               | 方向            | 描述                        |
| ------------------ | --------------- | --------------------------- |
| `message/send`     | Client → Server | 发送消息                    |
| `message/ack`      | 双向            | 消息确认或失败通知          |
| `message/history`  | Client → Server | 拉取历史记录（分页、游标）  |
| `message/event`    | Server → Client | 即时消息推送（单聊/群聊）   |
| `message/offline`  | Server → Client | 登录后下发离线消息          |

#### 发送消息
```json
{
  "id": "msg-58123",
  "type": "request",
  "command": "message/send",
  "payload": {
    "conversation_id": "room-001",    // 群聊 ID 或双方会话 ID
    "target": {
      "type": "user",                 // user / group / broadcast
      "id": "u-10002"
    },
    "content": {
      "type": "text",                 // text / emoji / markup / custom
      "text": "Hello world!"
    },
    "attachments": [
      {
        "file_id": "f-abc123",
        "name": "image.png",
        "size": 2048
      }
    ],
    "meta": {
      "reply_to": "msg-57111",
      "priority": "normal"
    }
  }
}
```

#### 推送消息
```json
{
  "id": "msg-58123",
  "type": "event",
  "command": "message/event",
  "payload": {
    "status": 200,
    "message": {
      "message_id": "msg-58123",
      "conversation_id": "room-001",
      "sender_id": "u-10001",
      "sent_at": 1730795220,
      "content": {
        "type": "text",
        "text": "Hello world!"
      }
    }
  }
}
```

### 2.4 文件传输

文件传输采用两阶段协议：先在控制通道协商元数据，再通过单独通道传输数据块。

| 命令                     | 方向         | 描述                                 |
| ------------------------ | ------------ | ------------------------------------ |
| `file/request`           | Client → Server | 文件传输申请，返回传输通道信息       |
| `file/accept`            | Server → Client | 同意传输，返回 `transfer_id`、端口   |
| `file/chunk`             | 双向         | 二进制通道内的数据块结构（非 JSON） |
| `file/ack`               | 双向         | 块级别确认、断点续传控制             |
| `file/cancel`            | 双向         | 终止传输，释放资源                   |

#### 协商流程示例（控制通道）

客户端申请：
```json
{
  "id": "file-req-1",
  "type": "request",
  "command": "file/request",
  "payload": {
    "file_name": "report.pdf",
    "file_size": 5242880,
    "hash": "sha256:...",
    "target": {
      "type": "user",
      "id": "u-10002"
    },
    "resume": {
      "transfer_id": "tr-001",  // 非空表示断点续传
      "offset": 1048576
    }
  }
}
```

服务器响应：
```json
{
  "id": "file-req-1",
  "type": "response",
  "command": "file/request",
  "payload": {
    "status": 200,
    "transfer_id": "tr-54879",
    "mode": "passive",              // passive: 客户端连服务器；active: 建立直连
    "endpoint": {
      "host": "server.example.com",
      "port": 9500,
      "protocol": "tcp"
    },
    "chunk_size": 65536,
    "resume_offset": 1048576
  }
}
```

二进制通道中的数据帧格式建议使用自定义 TLV：

| 字段      | 长度 | 描述                     |
| --------- | ---- | ------------------------ |
| `type`    | 1    | 0x01 数据块；0x02 ACK    |
| `length`  | 4    | 小端，payload 长度       |
| `payload` | 可变 | 数据内容或 ACK 详情      |

ACK 结构（JSON 序列化后作为 payload）：
```json
{
  "transfer_id": "tr-54879",
  "offset": 1310720,
  "status": 200
}
```

### 2.5 系统控制与通知

| 命令                 | 方向            | 描述                    |
| -------------------- | --------------- | ----------------------- |
| `control/ping`       | 双向            | 链路探测                |
| `control/error`      | Server → Client | 异常通知（断连、限流）  |
| `notification/event` | Server → Client | 业务通知：好友请求等    |

## 3. 会话与鉴权

- 登录成功后，客户端需在每次请求的 `headers.Authorization` 中携带 `Bearer <token>`。
- Token 支持续期：服务器在 `auth/refresh` 返回新的过期时间与 refresh token。
- 服务器可在 `auth/kick` 通知客户端重新登录，并关闭连接。

## 4. 协议版本控制

- `headers.version` 指示客户端支持的协议版本号（如 `1.0`）。
- 服务器响应中可附带 `headers.supported_versions`，提示最低/最高兼容版本。
- 当版本不兼容时返回 `payload.status = 426`，并透出升级建议。

## 5. 加密与安全

- 控制通道默认走 TCP + 应用层加密。初期可使用预共享密钥（AES-GCM）对 `payload` 字段加密。
- 当启用 TLS 时，客户端需校验证书；无需额外应用层加密，但可保留签名校验。
- 文件通道支持可选加密：协商 `encryption` 字段（如 `aes-gcm`、`none`）。
- 全部报文应包含 `payload.signature`（HMAC 或数字签名）供完整性校验。

## 6. 错误处理与重试

- 失败响应返回 `payload.error_code`、`payload.error_message`。
- 客户端对于 `5xx` 或网络错误可指数退避重试，最多 3 次。
- 消息发送失败需伴随 `message/ack` 返回 `status != 200` 与失败原因。

## 7. 离线消息与同步

- 客户端登录后发送 `message/history` 请求，携带 `since` 游标。
- 服务器返回批量消息及新的 `next_cursor`。
- 若消息超过一定数量，服务器可分页返回，并在 `payload.has_more` 指示。

## 8. 扩展点

- `payload.content.type` 可扩展为 `voice`, `command`, `file-ref` 等。
- 命令命名遵循 `领域/动作`，也可用 `.` 分隔，例如 `message.send`。
- 协议文件后续可转为 JSON Schema 供客户端自动生成模型。

## 9.改进点

### 要实现shared 模块的标准化 ；shared/protocol 子目录结构建议与讨论

shared/protocol 是协议的核心模块，它需要被设计成一个自包含的 Python 包（package），以便 client 和 server 都能轻松导入使用。这能提升可维护性：通过子文件分责，减少单一文件过大；用 __init__.py 暴露公共 API（如 from shared.protocol import MsgBase）；并支持测试/文档生成。我们可以按功能分层组织子文件，参考草案的报文结构、命令定义和扩展点。

```
shared/protocol/
├── __init__.py       # 包初始化：暴露核心类/函数，如 import MsgType, encode_msg
├── commands.py       # 命令常量与枚举定义
├── errors.py         # 错误码与异常类定义
├── framing.py        # 编解码与分帧逻辑（JSON + 二进制）
├── messages.py       # 消息模型定义（Pydantic 或 dataclass）
├── validator.py      # 校验逻辑（Schema、签名、版本）
├── extensions.py     # 扩展点（如语音/自定义类型预留）
└── schemas/          # 子目录：JSON Schema 文件（可选，生成模型用）
    ├── base.json     # 基础报文 Schema
    ├── auth.json     # auth 命令 Schema
    └── ...           # 其他命令 Schema
```

### 每个子文件的详细职责与理由

`__init__.py`：

职责：包入口，from .commands import *；定义全局常量（如 VERSION='1.0'）；暴露工厂函数（如 create_msg(command, payload)）。
为什么加：使模块像库一样用（import shared.protocol as proto; proto.MsgType.LOGIN）。提升标准化：用户无需知内部文件。
完善点：加 docstring 解释使用（e.g., """Protocol package for IM system"""）。若用 typing，import TYPE_CHECKING 防循环导入。


`commands.py`：

职责：定义 MsgType 类或 enum（如 class MsgType: LOGIN = "auth/login"）；分组命令（auth、presence、message、file、control）；加 docstring 描述方向/描述。
为什么加：草案 2.1-2.5 命令多，分开定义防硬编码散乱。便于 IDE 补全和搜索。
完善点：用 enum.Enum 或 typing.Literal 强制类型。扩展：加 COMMAND_MAP = {"auth/login": AuthHandler}，便于 server/router 动态派发。


`errors.py`：

职责：定义状态码 enum（如 StatusCode: SUCCESS=200）；自定义 ErrorCode（如 ERR_INVALID_TOKEN=1001）；异常类（如 ProtocolError(Exception)）。
为什么加：草案 1.2/6 强调错误处理，分开管理统一（e.g., raise ProtocolError(StatusCode.BAD_REQUEST, "Invalid payload")）。
完善点：加 to_dict() 方法，转 JSON（payload.error_code）。完善草案 6：加 retryable 标志（e.g., 5xx 可重试）。


`framing.py`：

职责：编码/解码函数（如 def encode_msg(msg_dict: dict) -> bytes: return json.dumps(msg_dict).encode() + b'\n'）；处理长度前缀/TLV（文件 chunk 用 struct.pack）；async def decode_stream(reader: asyncio.StreamReader) 防黏包。
为什么加：草案 1.1 提到 framing.py，集中处理编码约定。分离二进制逻辑（e.g., for file/chunk: type(1B) + length(4B) + payload）。
完善点：加压缩选项（zlib.compress 若 payload 大）。集成加密：encode 前调用 utils.crypto.encrypt_payload。测试：加 unit test 模拟 stream。


`messages.py`：

职责：Pydantic 模型定义（如 class BaseMsg(BaseModel): id: str; type: str; ...）；子类 per 命令（如 class LoginPayload(BaseModel): username: str; ...）；全用 model_validate()。
为什么加：草案报文/payload 复杂，模型强制结构（e.g., timestamp: int = Field(gt=0)）。生成示例 JSON。
完善点：加 alias（如 "conversation_id" -> "conv_id" 缩减大小）。若无 Pydantic，用 dataclasses + manual validate。


`validator.py`：

职责：校验函数（如 def validate_version(headers: dict) -> bool）；签名 check (HMAC.verify)；Schema 校验（pydantic_validator 或 jsonschema）。
为什么加：草案 4/5/6 需版本/签名/错误校验，集中防散乱。返回 ProtocolError。
完善点：集成 drafts 5：add def validate_signature(payload, key)。异步版 for server。


`extensions.py`：

职责：预留自定义（如 class VoiceContent(BaseModel): ...）；扩展 content.type（如 "voice"）。
为什么加：草案 8 提到扩展点，空文件起步，便于未来加语音/文件-ref 而无需改核心。
完善点：初始空，docstring 说明“Add custom msg types here”。若用，注册到 commands.py。


schemas/ 子目录（可选）：

职责：JSON Schema 文件（base.json 为报文骨架），用 pydantic_to_schema 生成。
为什么加：草案 8 建议转为 Schema，便于 API 文档/客户端生成（e.g., OpenAPI 工具）。
完善点：若不需，删子目录放 messages.py 内。完善：用 tools 如 pydantic-jsonschema 生成。