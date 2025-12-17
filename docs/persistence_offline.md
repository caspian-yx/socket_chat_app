# 持久化与离线消息改造说明

## 存储层

- `InMemoryRepository` 现由 `SQLiteStore` 提供驱动，存储内容覆盖：`users`、`sessions`、`presence`、`messages`、`rooms`、`room_members`、`offline_queue`。
- 默认数据库文件位于 `data/server.db`，可通过 `SERVER_DB_PATH` 环境变量覆盖。
- 所有服务仍复用原仓储接口，因此业务调用无需改动即可获得持久化能力。

## 离线消息

- `MessageService` 若发送失败，会将事件 JSON 写入 `offline_queue`。
- `OfflineDispatcher` 监听登录事件（`AuthService` 在认证成功时触发 `notify_user_online`），异步读取离线消息并尝试推送。
- 如果推送时用户再次离开，消息会重新入队，等待下一次登录。

## 回归测试

- 新增 `tests/test_repository.py`，验证消息入库与离线队列取出逻辑，可通过 `pytest` 运行。
