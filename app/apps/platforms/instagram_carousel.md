---
id: instagram_carousel
name_zh: Instagram Carousel
name_en: Instagram Carousel
emoji: 🎞️
region: en
content_type: text_post
max_script_chars: 1800
---
Platform writing rules (Instagram Carousel — swipeable cards):

**Why carousel beats single-image post**: IG's algorithm rewards dwell time. A multi-card carousel gives viewers 5-10x more reasons to linger, which triggers the algorithm to push the post further. 2026 engagement benchmarks: carousels ~1.92% vs Reels ~0.50% vs static ~0.45%. **Mixed-media carousels** (static cards + 1-2 short video/motion clips) push the number higher still (~2.33% vs ~1.80% for image-only) and adding an audio track pushes the post into the Reels feed too.

**The format**: 1-20 cards (IG raised the cap from 10 → 20 in Aug 2024; **7-10 cards is still the performance sweet spot**). Each card typically 1080x1080 (square) or 1080x1350 (portrait). **Portrait (4:5) gets ~20-25% more feed real estate and is preferred for text-heavy educational carousels; square is fine for brand/lifestyle.** The first card's aspect ratio **locks all subsequent cards** — plan ratio once, up front.

**Card 1 (the cover)** — this is the only card most non-followers see in feed. Treat it like a billboard: slide 1 carries ~80% of the weight, and viewers decide in 2-3 seconds whether to swipe.
- **Title line**: 5-10 words, bold, centered. Median top cover: 7 words.
- **Subtitle / kicker**: 3-6 words, smaller type. "A thread 👉" / "Swipe for the breakdown" / "(Save this)".
- Cover must **promise a payoff**. No decoration-only covers.
- **Archetypes** (pick one):
  - **Numbered list** ("7 mistakes I made at $1M ARR", "5 Ways to Style This Blazer")
  - **How-I story** ("How I write in 30 minutes a day")
  - **Contrarian** ("Most PMs measure the wrong thing. Here's why.")
  - **Problem-Solution** ("Struggling with [X]? Swipe for [Y]")
  - **Curiosity-gap** ("This 10-second change doubled our engagement…")

**Middle cards (cards 2 through N-1)** — one idea per card:
- **Headline on each card**: 2-6 words, the card's thesis.
- **Body copy per card**: 15-40 words max. Carousel readers swipe fast — long paragraphs kill it.
- Mix formats: standalone text cards, text+supporting image, text+chart, text+screenshot. Pure text cards need STRONG typography as the visual.
- Number the cards ("3/7" in a corner) for list carousels. Skip numbering for narrative/story carousels.
- **Persistent card chrome**: handle watermark (bottom corner) + small progress indicator ("3/7") + right-pointing swipe arrow `➡️` on every non-final card. **Every card must read standalone** — viewers regularly screenshot a single card and share to Stories, so the watermark is your attribution.
- **Watch the mid-carousel sag**: engagement drops after card 3 and rebounds near card 8. Re-hook at cards 4-5 with a callout stat, a contrarian aside, or "but here's the part most people miss." Don't coast through the middle.
- Cliffhangers between cards keep them swiping ("but the real reason is 👉").

**Final card — two valid formats** (pick based on the carousel's intent):
- **Pure CTA** (~60% of top carousels): single dominant instruction.
- **Recap + CTA** (~40%, often stronger): list all N card titles with checkboxes, then the single primary CTA below.

**CTA hierarchy** (ranked by 2026 algorithmic weight):
1. **Comment-gated DM-keyword** — highest leverage. DM sends are weighted 3-5x a like. Format: *"Comment WORD — I'll DM you the [template/checklist]."*
2. **Save** — strong signal: *"Save this for later 🔖"*
3. **Share** — great for K-factor: *"Send this to a [founder/friend] who needs it"*
4. **Follow for more** — weakest of the strong four, but legitimate: *"Follow @handle for weekly [topic]"*
5. Avoid **link-in-bio** as the primary CTA — IG deprioritizes off-platform clicks. DM-keyword flows (Manychat-style) are the modern replacement.

**The caption** (sits below the carousel):
- First 125 chars show before "more" — this is a second hook. Use it.
- **Hook-echo rule**: the caption's first line should echo the cover headline (verbatim or near-paraphrase). That reinforcement earns the "more…" tap.
- Total caption: 150-400 words for value-driven posts. Longer captions (up to 2,200 chars) work for personal-story posts.
- Plain text, no formatting (IG doesn't render markdown). Line breaks every 1-3 sentences for whitespace.
- End caption with the SAME CTA you used on the final card — reinforcement.

**Hashtags**: 3-8 niche hashtags. **Placement in caption OR first comment is algorithmically equivalent** (Instagram @creators confirmed indexing parity) — pick based on caption aesthetics: first comment for a cleaner feed look, caption if hashtags are part of brand voice. Specific over generic ("#solofounder" not "#business"). Banned / generic hashtags (#love, #instagood) actively hurt reach.

**Emoji use**: IG audience is emoji-tolerant. 2-4 per caption is normal. On cards, use sparingly — type should carry the weight, emoji is accent (👉 🔖 💡 ✨ ➡️ work; decorative 🔥💯 look amateur).

**Visual direction** — describe cover + at least 3 mid-card visuals in image_hint fields:
- Cover: high-contrast title card, brand color palette, single focal element.
- Card with data: chart mockup, callout number in big type.
- Card with example: screenshot / real artifact (an email, a tweet, a dashboard).
- Consistency: same font family, color palette, layout grid across ALL cards.
- Handle watermark + progress indicator + swipe-arrow chrome on every non-final card.

**Anti-patterns**:
- 10 cards of loosely-related tips with no arc
- Too much copy per card (anything over 50 words — the swipe is faster than reading)
- Middle cards that sag (no re-hook between cards 3-7)
- Link-in-bio as primary CTA
- Generic CTA ("Like if you agree!")
- Inconsistent card template between card 1 and the final card (breaks visual trust)
- Mixing aspect ratios mid-carousel (first card's ratio locks the rest)

**Output format**: In sections, each section's `text` is ONE card's copy (title + subtitle + body). Include `image_hint` per card describing the visual. The final section is the caption + CTA.
