---
id: discord
name_zh: Discord
name_en: Discord
emoji: 💬
region: en
content_type: text_post
max_script_chars: 1800
---
Platform writing rules (Discord server post / announcement):

**What Discord content actually is**: Unlike LinkedIn or Substack, Discord posts live inside a **community you already belong to**. Your readers are members who opted into the server. This changes everything — you're not earning attention from strangers, you're respecting the time of people who trusted you enough to join.

**Channel types** — pick your lane:
- **#announcements** (Announcement Channel, the channel type): broadcast-style, members get a notification. Can be *followed* from other servers (the message gets relayed into their #news channel), so tone has to read well **out of context** — no server-specific inside jokes at the top. Rate limit: **10 publishes/hour** (Discord's cap; this is why batching updates matters).
- **#general / community channels**: conversational. Questions, shared wins, polls. Much lower formality.
- **Forum channels** (channel type): a post needs a **title + tags + body**. Persistent, Reddit-style Q&A or showcase content. Each post IS a thread.
- **Threads on a message** (message type — don't conflate with forum channels): ephemeral discussion attached to one message. Great for opening discussion under an announcement. **Auto-archives at 24h by default** (configurable: 1h / 24h / 3d / 7d). Extend to 3d/7d for weekly recap threads.
- **Scheduled Event + Stage channel**: tied to a live event (AMA, workshop). Create the Scheduled Event first; members who hit "Interested" get auto-reminders.

**Message length and format**:
- Hard cap: **2,000 chars per message** (4,000 for Nitro boosters). Longer posts = multi-message or move to a blog + link.
- Supports **markdown**: `**bold**`, `*italic*`, `__underline__`, `~~strikethrough~~`, `# H1`, `## H2`, `### H3`, `> blockquote`, `` `inline code` ``, ``` ```code blocks``` ```, `[link text](url)`, `||spoiler||`.
- **`-# subtext` footer syntax** (under-used goldmine): renders as small, de-emphasized text. Use for disclaimers, attribution, "posted via bot", source links, version notes. Keeps the main message clean while still carrying the metadata.
- **Headers (# ## ###) are the real game-changer**: Discord renders them large and bold — far more feed-prominent than plain `**bold**`. Use `# Title` for announcement headlines.

**Identity: human vs webhook** (important distinction):
- **Founder / Community Manager voice** → post from a **personal user account** (online presence indicator, profile-hover, DM-able). This is how members recognize and trust you.
- **Automated or scheduled** (changelog, build status, social-media mirrors, RSS feeds) → use a **named webhook** with its own avatar ("📦 Release Bot", "📢 Changelog"). Members expect these to be un-replied-to and un-DM-able.
- Don't blur the two. A human-voice post from a webhook feels cold; a bot-style status update from a real user feels robotic.

**Announcement post structure** (for #announcements channels):

1. **Headline as H1**: `# [Verb]: [Specific outcome or product]` — e.g.
   `# Launching: v2 of our Slack integration` / `# This Wednesday: Office hours with [guest]`
2. **One-sentence hook**: what this means for the reader, in plain language
3. **The meat** (3-6 lines): what's changing, what's new, what they can do. Numbered or bulleted. No walls of text.
4. **Call to action**: link to full page, Scheduled Event URL, thread reply ask — ONE clear next step
5. **Optional `-# footer`**: small note, thanks, source link, or "-# Full release notes: [link]"

**Thread etiquette after announcements**:
For any announcement likely to draw >5 replies, **open a thread directly off the post** so the #announcements channel stays broadcast-only. The thread inherits the context; the channel stays readable. For persistent discussions (weekly office-hours recaps, monthly retros) extend the auto-archive from 24h to 3d or 7d.

**Three additional announcement templates** you should reach for:

- **Welcome ritual** (pinned first post in a new channel):
  ```
  # Welcome to #[channel-name]
  This channel is for [one-sentence purpose].
  - ✅ Start here: [link to rules / README]
  - ✅ Pick your role: [link to #roles channel]
  - ✅ Post your intro: [link to #introductions]
  -# Not sure where to post? Ask in #general.
  ```
  Research: members who get a first message in <15 minutes of joining show substantially higher long-term engagement.

- **Changelog / version bump**:
  ```
  # v2.3 is live
  **Headline change in one line.**
  ## Added
  - Feature A
  - Feature B
  ## Fixed
  - Bug C
  - Bug D
  ## Changed
  - Behavior E now does F
  -# Full release notes: [link]
  ```

- **"You said, we did" monthly recap**: a trust-building post that lists member-requested items shipped this month. Call out the requester by @user when possible ("Thanks @ana for flagging this"). One of the highest-signal post types for community retention.

**Community post structure** (for #general-like channels):
- More casual. Dropping a thought, sharing a win, asking a question.
- Open with the specific, not the preamble: "Just shipped [thing]. Took 4 weeks. Biggest lesson: [X]."
- Invite conversation: end with an actual question that has multiple valid answers, not a rhetorical one.

**@mention etiquette** (critical — misuse kills trust):
- **`@everyone`** — only for truly universal announcements (major launches, security issues). Overuse trains members to mute the channel.
- **`@here`** — online members only. Use for live events starting soon.
- **`@role`** (e.g. `@Founders`, `@Alpha-Testers`) — preferred for targeted announcements. Restrict `#announcements` posting to admins so role-pinging stays controlled.
- **No ping at all** — default for most posts. Let interested members find it via channel subscription.

**Reactions as structured polls** (not just 👀-if-you're-in):
The modern pattern uses **explicit multi-option reactions** for a lightweight poll:
```
RSVP for Friday's event:
🟢 Yes, I'll be there
🟡 Maybe
🔴 Next time
```
This is more useful than a single "👀 if you're in" — members communicate nuance with one click, and the CM can read the room at a glance. Custom emoji with explicit names (`:vote_yes:` / `:vote_no:`) aid clarity.

**Embeds** (rich cards, typically bot-sent):
- Pack: title, description, author, thumbnail, fields, footer, color bar on left.
- Used for: polls, event cards, milestone badges, structured updates.
- If content is going through a bot, suggest embed format with explicit fields; otherwise plain markdown with `#` headers and `-#` footer lines.

**Emoji and reactions**:
- Custom server emoji reinforce community identity. Generic emojis fine but sparingly.
- End appropriate posts with a reaction CTA (see "Reactions as structured polls" above — single-emoji "🔥-if-you-agree" reads as low-effort; multi-option reads as intentional).
- Reactions are Discord's share-economy equivalent — design posts to earn them.

**Length and cadence**:
- Announcements: short, under 500 chars ideal. Link out to long-form elsewhere (Notion, blog, Substack).
- Don't post daily in #announcements — it trains members to mute. Weekly max for most servers (and rate-limited at 10/hour by Discord anyway).
- For community channels: natural flow, no rhythm constraint.

**Anti-patterns**:
- `@everyone` for non-critical posts
- Walls of text without headers (`#`/`##`) or bullets
- Corporate tone ("We are pleased to announce that...")
- Cross-posting the exact same text from Twitter/LinkedIn unchanged (community members see this as broadcast — adapt the tone to the server's culture)
- Hashtags (Discord has no hashtag search — wasted chars)
- Naked product links without context
- Signing off with a marketing-style "Let us know your thoughts!" — instead ask the specific question OR invite the reaction-poll vote
- Confusing forum channels with threads on a message (they're different primitives; each needs its own structure)

**Cross-posting discipline**:
- Discord content should be **adapted**, not copy-pasted from other platforms. Even if the source is a LinkedIn post, on Discord:
  - Drop the feed-hook patterns (the member is already inside — no feed)
  - Add a specific question for this community's vibe
  - Thank / tag the person whose question inspired it, if applicable
  - Reference ongoing server jokes / prior discussions when natural
- For announcement-channel posts that will be *followed* by other servers, keep the top generic enough to read out of context.

**Output format**: For announcement posts, the first section is the H1 title + hook. Middle sections are the meat (one idea per section). The last section is the single CTA + optional `-#` footer.
