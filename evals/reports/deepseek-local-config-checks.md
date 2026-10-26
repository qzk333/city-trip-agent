# DeepSeek 适配与本机配置保存验证

日期：2026-10-23。

## 兼容性依据与修改

用户填写 `https://api.deepseek.com` 和 `deepseek-flash`。该地址和模型名符合当前官方文档。
项目首轮使用 `tool_choice=required`，但此前未为 DeepSeek 显式设置思考模式。
[官方 Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)
说明默认启用思考模式，且此模式不支持 required / named tool choice。

现在只为 `api.deepseek.com` 官方域名的规划和连接测试添加
`thinking={"type":"disabled"}`；不自动修改模型 ID，不影响其他服务商。
规划失败信息区分 HTTP 状态、输出截断、工具调用缺失、JSON 解析和候选地点校验，
不回显服务商的原始报错、模型文本或凭据。

## 配置保存

- 默认使用 sessionStorage；用户显式勾选后使用 localStorage，界面说明包含 Key 且本地不加密。
- 从一种模式切换到另一种时移除旧副本，清除入口删除两种存储中的副本。
- 高德 JS 凭据继续留在浏览器；模型与高德 Web Key 按请求传递，不写入后端数据库。

## 检查结果

- 79 项后端测试通过，包含官方域名的非思考请求、相似域名隔离、错误分类和脱敏。
- TypeScript、生产构建、Prettier 与 Ruff 检查通过。
- 20 项桌面/手机 Chromium 流程通过。新增测试实际关闭并重启使用同一用户配置目录的浏览器，验证凭据恢复、取消记住、清除以及清除后再次重启不恢复凭据。
- 开发服务已重启加载适配代码。

模型协议测试使用模拟响应。没有取得或读取用户 API Key，也没有调用真实付费 DeepSeek
服务，因此还不能声称原始失败已通过在线重试得到确认。
