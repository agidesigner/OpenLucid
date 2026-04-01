---
app_id: topic_studio
name: 选题工坊
name_en: Topic Studio
slug: topic-studio
description: 围绕策略单元，结合知识与素材，生成结构化选题方案
description_en: Generate structured topic plans based on strategy units, knowledge base and assets
icon: "💡"
category: content_creation
task_type: topic_generation
required_entities: [offer, strategy_unit]
required_capabilities: [retrieve_context, select_knowledge, generate_topics]
entry_modes: [global, strategy_unit]
status: active
required_model_types: [text_llm]
---
