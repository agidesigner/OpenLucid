---
# DEFAULT — to customize, edit $STORAGE_BASE_PATH/platforms/reddit.md (typically docker/uploads/platforms/reddit.md on the host). Editing this shipped file will conflict on `git pull`. See SELF_HOSTING.md.
id: reddit
name_zh: Reddit
name_en: Reddit
emoji: 🤖
region: en
content_type: text_post
max_script_chars: 3000
---
Platform writing rules (Reddit post):

**Community-first mindset**: Reddit users have a strong allergy to marketing. The #1 rule: **sound like a real community member, not a brand**. If the post smells like an ad, it gets downvoted into oblivion or removed by mods.

**Pick the right subreddit context**: Each subreddit has its own tone, rules, and pet peeves. Before writing, the AI should adapt to the intended subreddit's vibe. Sub-specific rules that actually matter:

- **r/startups**: Posts **MUST** include the literal phrase `I will not promote` (usually in the title or first line). Automod removes posts without it. Zero product links tolerated.
- **r/entrepreneur / r/SaaS / r/smallbusiness**: One product mention at the very end is acceptable if the post is 90% genuine lesson/story.
- **r/indiehackers / r/solopreneur**: Build-in-public culture — a product link in paragraph 1 is tolerated when the post is a real founder story (numbers, failures, process).
- **r/marketing**: Practitioner-focused, hates "digital marketing tips" slop. Specific campaigns + metrics win.

**Title rules**:
- Be specific and useful, not clickbait. No CAPS (top posts: only 3% have any ALLCAPS). No emojis (1%).
- Top-performing title pattern: **compound narrative** — "[specific action/number]. [emotional consequence]"
  - "Sold 340 lifetime deals for $149 each. 18 months later I regret every one."
  - "Spent $300k on a healthcare app that nobody uses."
  - "Solo founder, $20k MRR, zero ads, zero employees. Here's exactly what worked."
- Longer titles (15+ words) are fine — 35% of top titles run long.
- Specific-loss narratives ("I spent $X", "I lost $X", "I regret doing X") are disproportionately represented.

**Post body structure**:
- **Self-ID inline, not labeled**: 0% of top posts use a `Background:` or `About me` header. Credentials go in the first real sentence: *"I run a small service business and for 3 years I've been charging…"* / *"I've been doing sales consulting for 8+ years now and…"* First-person verbs, not formal appositives.
- **Meat** (main insight): The actual value. Specific numbers, real examples, honest lessons. Use markdown headings if the post is long.
- **Caveats / limitations**: Redditors love honesty. "This might not work if..." builds credibility.

**TL;DR** — **Optional and rare in top posts (~2% usage)**. If you include one, place it at the **TOP** as a one-line hook, not the bottom. Most high-upvote posts have no TL;DR.

**Markdown support**: Reddit supports markdown — use **bold**, *italic*, headings (#, ##), lists (-), code blocks (`). But prose dominates (76% of top posts) — don't force bullets when a narrative works.

**Length**: **200-800 words is the real sweet spot** (median top post ≈ 270 words; 56% of top posts are under 300 words). Over 1,000 words pays off only for genuine long-form teardowns. Over 2,000 words is almost never upvoted.

**What gets upvoted**:
- Specific numbers, case studies, lessons from failure
- Specific-loss narratives ("I spent $X", "I lost $X", "I regret doing X") — disproportionately dominant
- Counterintuitive observations with evidence
- Detailed frameworks or processes
- Honest accounts of what didn't work

**What gets downvoted / removed**:
- Pure self-promotion
- Generic advice ("Focus on your customers!")
- Shills disguised as stories
- Linking out to your own site/product without context (outside build-in-public subs)
- Rage-bait or drama

**Ending**: ~25% end with a genuine question, but **72% end with no CTA** — just the closing beat of the story. Both are valid. Don't force a question onto a post that ends stronger as a statement. When asking, be specific: "Has anyone else tried X? Did you see similar results?" (not lazy "What do you think?").

**Anti-patterns to avoid**:
- `Don't hedge with "remove if not allowed"` — top posters don't; it reads as a low-karma tell (0% of top posts use this).
- Don't add a formal `Background:` block — write credentials into the narrative.

**Self-promotion**: See per-sub rules above. The blanket "once at the end" rule is wrong — it depends on the sub.

**Output**: In sections, write in prose by default. Numbered/bulleted structure only when the content genuinely IS a list (steps, comparisons, pricing tiers).
