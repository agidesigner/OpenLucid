---
id: blog
name_zh: 博客文章
name_en: Blog Post
emoji: 📝
region: global
content_type: article
max_script_chars: 6000
---
Platform writing rules (long-form blog post for a brand's own site / Medium / Substack / WeChat 公众号):

**Purpose**: Long-form article designed to rank in search + build reader trust. Published on the user's own blog or brand newsroom. Success = first-page ranking for the target keyword + readers feeling the brand understands their problem deeply.

**Target length**: 1500-3000 words. Under 1000 words rarely ranks for competitive keywords. Over 4000 dilutes relevance — unless it's a deep comparison (which earns the length via structured sub-reviews).

**Search intent match**: Before writing, identify the user's likely search intent:
- **Informational**: "how to / what is / why does" → educational content, explain concepts
- **Commercial investigation**: "best X for Y / X vs Y / X review" → comparison, pros/cons, recommendations
- **Transactional**: "buy X / X pricing / X discount" → conversion-focused, clear offer
- **Navigational**: "brand name + feature" → straightforward, product-focused

**Title (H1)**:
- Include the target keyword in the first 60 characters
- Number / year / specific benefit increases CTR: "7 SEO Tactics for 2026 (That Actually Work)"
- Match the search intent (don't promise "best X" and deliver a tutorial)

**Meta description** (include in section metadata if possible):
- 150-160 characters
- Include the keyword once, naturally
- Write a compelling summary that answers "why should I click?"

**Keyword usage**:
- Target keyword: 5-10 times in a 2000-word article (1-2% density max)
- Semantic variations: use naturally throughout
- **Never** keyword stuff — Google penalizes it
- Place keyword in: title, first paragraph, at least one H2, URL slug, image alt text

**Readability**:
- Grade-level target: 8-10 (accessible, not dumbed down)
- Short paragraphs (2-4 sentences); break walls of text with lists and tables
- Active voice > passive voice
- Concrete examples > abstract theory
- First person ("we", "I") or second person ("you") increases engagement

**E-E-A-T signals** (Google ranking factors):
- **Experience**: share first-hand experience ("When I ran this test on 200 sites...")
- **Expertise**: technical/domain-specific detail
- **Authoritativeness**: cite data, link to authoritative sources
- **Trustworthiness**: admit limitations, cite sources, include author context

**Internal linking opportunities**: mark 2-3 places where a related article on the same site would fit, so the user can add links later.

**Image suggestions**: describe 2-4 ideal images (in image_hint fields) — hero at top, illustration for complex concepts, screenshot/chart for data, visual for the CTA area. Include alt text suggestions.

**Platform-agnostic output**: works on WordPress, Medium, Ghost, Substack, WeChat 公众号, 知乎, and any CMS. Just paste and format.

---

**Universal formatting conventions (apply to every blog post regardless of intent)**:

1. **Metric-dense opening**: if the input contains numerical data (GA metrics, market stats, product adoption numbers, financial figures), quote **2-3 raw numbers within the first 100 words**. Raw numbers are the single strongest credibility anchor. Never paraphrase "meaningful growth" when you can write "+47% YoY" (compare Reddit's "40% YoY increase", Manus's "147 Trillion tokens processed", "121M DAU"). Use the `extra_req` data points when provided.

2. **Sourced citations for external claims**: when citing any external data, link or attribute inline — "according to Statista, ..." or a superscript footnote `[1]` with a source line at the bottom. Unsourced stats read like AI slop.

3. **Attributed customer / expert quote**: every blog ≥1000 words should carry at least one **specific, attributed** quote. Format: "Quote text." — FullName, Title @ Company (with one concrete outcome metric if available, e.g. "33% of total platform revenue"). Anonymous testimonials = low trust.

4. **Per-segment "what's included" blocks** (when the post covers multiple user types): use explicit bullet lists headed by segment, e.g. "**For individuals:** checkmark next to username / access to Reddit Pro ... **For businesses:** watermark replaced ... profile flairs for AMAs ...". Reddit does this on every multi-audience product announcement.

(The universal non-promotional-paragraph rule lives in the BASE layer and is already in your system prompt — no need to restate here.)

---

**Structural templates by post intent — infer the intent from the user's topic/goal, then apply the matching template**:

**(a) Comparison / review / "best X" posts** (hints: "review", "vs", "best", "compare", "tool roundup"):
- Open with a 4-7 row markdown COMPARISON TABLE right after the intro. Columns: option name | best-for tag | key price or metric | one-line verdict. Gives skimmers 10-second value before committing.
- "How I tested" / "My evaluation approach" section BEFORE individual reviews. Share the concrete test task + 3-5 explicit criteria. Biggest single E-E-A-T signal.
- Each option gets one H2: 2-3 narrative paragraphs + "Liked / Didn't" two-list + 1-sentence verdict.
- Close with a "What I learned" section — 3-5 H3 cross-cutting takeaways. This is what separates a blog from a spec sheet.
- Optional "How to choose the right X for you" buying guide — 2-4 decision questions.
- Optional FAQ section — 5-8 Q&A pairs answering common follow-ups.
- Target length: 2500-4500 words (length is the whole point of a comparison).

**(b) Tutorial / how-to posts** (hints: "how to", "guide", "build", "set up", "tutorial"):
- Tighter — target 600-1200 words.
- Open with the concrete problem ("For many [audience], [pain] is..."), not abstract context.
- Use **role-based vignettes** — "the customer emails → the agent reads → you click Confirm" — rather than formal numbered steps, when the workflow is conceptual. Use numbered steps only when sequence literally matters (CLI commands, setup order).
- Second-person voice ("you", "your") throughout.
- Close with a **single clear** try-it-yourself CTA. No dual CTAs, no FAQ — keep focus narrow.

**(c) Product / feature announcement posts** (hints: "introducing", "launching", "announcing", "new feature"):

- **Choose a voice model** (pick one, commit):
  - *Founder-emotional* (Notion-style): "I'm excited to share..." / "It's always pained me to see..." — personal, first-person, warm
  - *Metric-data* (Manus / Stripe style): leads with raw numbers ("147T tokens processed", "analyzed 20K+ businesses") — research-credibility, third-person-plural
  - *Sober-corporate* (Reddit Inc / Anthropic style): "Today, we're announcing..." — clinical, policy-aware, opt-in / safety disclosures prominent
- Lead with a **direct announcement statement** in the first 80 words. Reddit: "Today, we're announcing a limited alpha test of verified profiles." Notion: "I'm excited to share that Custom Agents are finally available." Do NOT bury the lead behind a generic hook.
- **"How it works" section** with 3-5 concrete mechanics (what the user sees, what's automated, what the agent does). No hand-waving.
- **"What's included for [segment]"** bulleted breakdowns when feature affects multiple user types.
- **2-4 use case vignettes** — prefer **concrete customer examples with specific metrics** over abstract personas. Notion's pattern: "Ramp runs 300 Q&A agents across their knowledge base", "Remote saved 20 hours/week across their recruiting team", "Clay built an Incident Reporter that auto-escalates" — each names a real company + a real number + a real outcome. Generic vignettes ("a florist organizes photos") are weaker unless you have no customer data to cite.
- **Dedicated concern / safety / limitation paragraph** near the end. Notion calls this "A final note on AI safety"; Reddit calls it "Accounts must still follow all existing rules". This is **beyond** the universal non-promotional rule — it's a specific named subsection addressing the anxiety the announcement creates (data access / model accuracy / price / breaking change for existing users).
- **CEO / founder / product lead quote block**: 1-2 sentences attributed to a named executive. Only if the user's input includes such a voice or `reference` to paraphrase — do not fabricate quotes.
- **Availability section at end**: who gets it, when, platform requirements, pricing tier boundary. Tight factual close.
- Target length: 1000-1800 words.

**(d) Essay / research / perspective posts** (hints: "why we believe", "the future of", "lessons from", "a case for", "our take on"):
- Lead with a sharp thesis in the first paragraph — the one claim the rest of the post argues. No throat-clearing.
- Build in 3-5 numbered or named sub-arguments, each a H2.
- Support each argument with **at least one concrete example** (case, data point, quote). Abstract essays without examples read as empty thought-leadership.
- Opposing view / devil's advocate paragraph near the end — acknowledge the strongest counter-argument to your thesis, then refine the thesis in light of it.
- Close with a forward-looking question or concrete prediction (with a timeline if possible: "by 2027, ...").
- Target length: 1500-2500 words.
