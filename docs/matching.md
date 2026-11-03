# 页面配对与互不重叠分组

1. `extract_pptx`按原始shape顺序读取可编辑文本，包含组内shape和表格；不OCR、不先总结、不把截图交给分组模型。
2. Flash输出`matches`语义链接以及`unmatched_a/b`。链接区分equivalent、包含和partial overlap，附原文连续引文与自评confidence。
3. 将A页/B页作为二部图顶点，链接为边。共享任一顶点的边进行传递合并，形成连通分量。
4. 一个分量是一个评审组；同一页不能分到多个组。多对多分量内部只保留模型实际支持的边，不补齐所有cross-pairs。
5. 未连接的页必须恰好出现在unmatched一次；已经连接的页不能同时unmatched。独有内容允许不配对。

原文证据只接受原文、空白或排版引号等价定位，记录`evidence_*_span`；不做模糊语义拼接。只修引文格式的repair不能悄悄改变配对、覆盖类型或confidence。非法JSON、未知页、重复边和不完整清单会触发保留原尝试的修复。

原始prompt版本：`text-graph-v4-disjoint-en`，见`prompts/alignment.en.txt`。原生文字提取与图像美学是分离任务，防止把视觉偏好泄漏到内容配对。

缓存应绑定两边PPTX SHA、方向、模型请求标识、提示词与参数。更换原稿、协议或A/B方向必须拒绝静默复用；审美结果只能恢复同一次中断尝试，不能用于冒充独立新rollout。
