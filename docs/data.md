# 合格训练集与数据溯源

HF仓库保持私有：`yzxjb/ppt-sft`。新default config为`qualified_sft`，版本目录`qualified/20261101-v1/`。

冻结筛选结果：2,605候选轨迹中排除108个完整案例，保留2,497条轨迹、4,325训练窗口；其中2个窗口仅增加重复回答的loss mask。原train/validation文件没改，本导出把保留的旧验证案例并入train，所以没有独立holdout。

教师：Qwen3.8-27B 2,445条、Flash Q8_0 52条；材料英文1,764、中文733，覆盖20领域。3,373张真实附件图、3,414次图引用。训练PPTX要求英文。

`train.jsonl.gz`保留`id/messages/tools/images`，每条message的`loss`及原有上下文mask不变。`images`由机器绝对路径改为相对release路径；下载后`prepare-local`校验文件SHA再转换为本地路径。不会把历史消息里出现的路径文本改写，保持模型训练目标一致。

`cases.jsonl.gz`提供2,497案例、teacher/domain/原归档指针；`sources.jsonl.gz`保留每个窗口与真实provider/native会话的来源；`token-lengths.jsonl.gz`和`provenance/`保留原冻结审查记录。`image-map.jsonl`说明唯一图片的来源、相对路径、SHA及字节数。`manifest.json`是完整可校验文件清单。

此前结构检查覆盖全部候选PPTX，全部渲染成功；对标记案例做了选择性的视觉检查，不把它描述为逐页完整事实审计。当前上传不新增采样、风格或评审实验数据，也不把失败尝试混成合格训练例。

工具定义是结构化list，消息content为string；训练loader必须保留逐message loss字段。字面量`<image>`被保护为`\uE000PPT_LITERAL_IMAGE_TAG\uE001`，用`tools/sft_template_compat.py`恢复，避免把代码/日志里的标记误当图片附件。

历史raw/index/catalog仍保留在HF原位置，和本次qualified subset是不同层级。输入材料遵循原来源条款，没有为所有材料指定统一许可证。仓库凭证只存在进程环境或隐藏stdin，不作为数据文件。
