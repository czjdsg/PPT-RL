# 推理与部署

生成与评审是两个阶段。生成端使用 native Pi coding-agent SDK 1.4.0，通过 Anthropic Messages 接口调用已部署模型；模型可执行本地工具并写 PPTX。评审端在生成结束后独立渲染、配对和比较，评审截图不会回灌到生成会话。

历史采集使用 Qwen3.8-27B BF16（vLLM）以及 `qwen3.8-flash-next-unsloth-q8_0`（Strata 0.2.7 / engine 0.1.52，Q8_0 GGUF + BF16 vision projector）。Flash 历史部署是双卡分层，不能称为 vLLM TP2。Pi声明上下文131072；实际后端上下文另行记录。这个仓库不打包后端引擎、模型权重或机器GPU绑定。

vLLM 原生 Messages 后端可用可配置 keepalive gateway：

```bash
python -m ppt_rl.gateway --upstream http://127.0.0.1:8000 \
  --port 18767 --model Qwen3.8-27B --log-dir ./runs/gateway
```

这个适配器是 **原生 Anthropic 路由透传**，不是通用 OpenAI-to-Anthropic 转换器。它显式传递 `enable_thinking`、保留工具/图像块并发送SSE ping。Flash原生接口可直接使用。端点、模型名、Node路径都由配置指定；远程评美密钥只通过环境变量。

主要产物：`result.json`、`trace-manifest.json`、`logs/events.jsonl`、`logs/sessions/`、`logs/requests/*.json.gz`、`request-audit.jsonl`、system prompt、read audit与PPTX。累计partial消息不重复落盘，原始delta和final消息保留。自动compaction走native独立路径，普通onPayload审计不覆盖所有compaction调用，因此保留native session/compaction记录，不能用request-audit单独声称完整网络捕获。

source read示例（Pi工具参数）：

```json
{"path":"materials/source.pdf","render":true,"pages":"1-2"}
```

```json
{"path":"materials/source.pdf","render":true,"pages":"3","crop":"0,0,320,240"}
```

`extract_images=true`提取原PDF内嵌图；坐标单位PDF points。source images放在`materials/`。生成预览的读取按工具包装和路径策略限制，这不是隔离任意恶意shell的完整安全沙箱。

SQLite适配器按任务claim/heartbeat/complete，180秒租约、20秒心跳，过期重领并拒绝旧worker回传；停止采样和上传数据是独立操作。保留`worker_core.py`及`queue_worker.py`便于复用原部署协议；初始化前生成新的`CODE_FILES.json`，固定任务输入包SHA和模型探针证据。单案例入口优先使用`ppt-rl infer`。

风格对照只增加原始template及其source PDF，要求仅参考视觉设计、事实内容来自原材料。两组用相同基础query/seed/参数，保留所有预先指定尝试；原图与模板可见，生成图像反馈保持关闭。固定物理workspace后移动到各尝试目录，可使成对实验的cwd系统提示保持一致。
