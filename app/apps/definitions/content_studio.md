---
app_id: content_studio
name: 内容创作
name_en: Content Studio
slug: content-studio
description: 生成图文笔记、长文、推文等多渠道文字内容（小红书、公众号、X Thread 等）
description_en: Generate posts, articles, and threads for text-based channels (Xiaohongshu, WeChat, X/Twitter, etc.)
icon: "📝"
category: content_creation
task_type: content_generation
required_entities: [offer]
required_capabilities: [retrieve_context, text_generation]
entry_modes: [global, strategy_unit]
status: active
required_model_types: [text_llm]
---

# Content Studio

专注于文字内容的创作工具。与 Script Writer（视频脚本）共用 Composer 架构，但产出形态不同：

- **图文笔记**：小红书（标题+正文+标签）
- **长文**：公众号（导言+论证+结论）
- **推文**：X/Twitter Thread、Reddit 帖子、LinkedIn 长贴

产出的 creation 直接复制使用，无需 TTS / 视频合成等下游步骤。
