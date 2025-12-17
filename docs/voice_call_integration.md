# 语音通话功能集成说明

## 服务器端集成

要在服务器端启用语音通话功能，需要在服务器的路由器中注册语音通话服务的处理器。

### 步骤1：导入VoiceService

在 `server/main.py` 或 `server/core/server.py` 中：

```python
from server.services.voice_service import VoiceService
```

### 步骤2：创建VoiceService实例

```python
voice_service = VoiceService()
```

### 步骤3：注册路由处理器

在CommandRouter中注册语音通话相关的命令处理器：

```python
router.register(MsgType.VOICE_CALL, voice_service.handle_call)
router.register(MsgType.VOICE_ANSWER, voice_service.handle_answer)
router.register(MsgType.VOICE_REJECT, voice_service.handle_reject)
router.register(MsgType.VOICE_END, voice_service.handle_end)
router.register(MsgType.VOICE_DATA, voice_service.handle_voice_data)
```

### 步骤4：处理用户断线

在用户断线时，需要结束其正在进行的通话。在连接管理器的断线处理逻辑中添加：

```python
async def on_disconnect(user_id: str, ctx: ConnectionContext):
    await voice_service.user_disconnected(user_id, ctx)
```

## 客户端依赖安装

语音通话功能需要以下Python库：

### 必需依赖

```bash
pip install pyaudio
```

### 可选依赖（用于Opus编解码，提供更好的音质）

```bash
pip install opuslib
```

注意：
- **Windows**: PyAudio安装可能需要先安装PortAudio
- **Linux**: 需要安装 `portaudio19-dev` 或 `portaudio-devel`
- **macOS**: 可以通过 `brew install portaudio` 安装

如果没有安装 `opuslib`，系统会自动回退到PCM编码（未压缩音频）。

## 使用说明

### 双人语音通话

1. 在聊天界面选择要通话的用户
2. 点击"📞 语音通话"按钮
3. 对方会收到来电提示，可以选择接听或拒绝
4. 接听后开始语音通话
5. 点击"挂断"按钮结束通话

### 群聊语音通话

1. 在聊天界面选择要通话的群聊房间
2. 将"发送至"选项切换为"房间"
3. 输入房间ID
4. 点击"📞 语音通话"按钮
5. 群内所有成员都会收到来电提示
6. 接听的成员可以加入群聊语音通话
7. 点击"挂断"按钮退出通话

## 协议说明

### 语音通话命令

- `voice/call` - 发起通话
- `voice/call_ack` - 通话请求确认
- `voice/answer` - 接听通话
- `voice/answer_ack` - 接听确认
- `voice/reject` - 拒绝通话
- `voice/reject_ack` - 拒绝确认
- `voice/end` - 结束通话
- `voice/end_ack` - 结束确认
- `voice/data` - 语音数据包
- `voice/event` - 语音事件（来电、连接、挂断等）

### 音频配置

默认配置：
- 采样率：48000 Hz
- 声道数：1（单声道）
- 编码：Opus（如果可用）或 PCM
- 帧长：20ms

## 注意事项

1. **防火墙配置**：确保服务器端口可以接收语音数据包
2. **网络延迟**：建议在低延迟网络环境下使用
3. **音频设备**：确保麦克风和扬声器设备可用
4. **权限**：某些系统可能需要麦克风访问权限
5. **性能**：语音通话会占用额外的CPU和带宽资源

## 故障排查

### 无法启动通话

- 检查是否安装了 `pyaudio`
- 检查麦克风和扬声器设备是否可用
- 查看日志中的错误信息

### 音质问题

- 尝试安装 `opuslib` 以使用Opus编码
- 检查网络连接质量
- 调整采样率和帧长参数

### 服务器端问题

- 确保VoiceService已正确注册到路由器
- 检查服务器日志中的错误信息
- 确认防火墙规则允许通信
