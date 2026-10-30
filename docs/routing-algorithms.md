# How `aiu --advise` picks a subscription

`aiu --advise` answers one question: **which flat-fee subscription should get
the next piece of work?** This page explains, in plain terms, the algorithms
we studied, and why `aiu` uses the one it does.

## The problem in one paragraph

Claude Max, Codex and Gemini are flat fees. Using them costs nothing extra,
and whatever quota you don't use is **thrown away at the reset**. DeepSeek
and OpenRouter charge per token. So the goal is simple: let as little free
quota as possible expire unused, and pay for as little work as possible.

Think of each subscription as a fridge that is emptied and restocked on a
fixed day. The question is never "which fridge is fullest?" but **"which
fridge will still have food in it on restocking day?"** That food is free,
and it is about to be thrown out.

## The example that exposed the problem

Snapshot from 30 October 2026, around 09:00 UTC:

| Subscription | Weekly quota left | Resets in | At your pace… |
|---|---:|---|---|
| Claude | 14% | 6 h | ~11% of the week is thrown away |
| Gemini | 30% | 4 d 22 h | runs dry in ~21 h, then 4 days with nothing |
| Codex  | 19% | 5 d 1 h | runs dry in ~11 h, then 4½ days with nothing |

The old `aiu` said: *"Claude is filling up, move the surplus to Gemini."*
That was backwards: it took work away from the one subscription about to
waste quota and gave it to one about to run out.

## The algorithms

### 1. Most headroom (the old rule, replaced)

> "Send work to whichever subscription has the biggest percentage left."

It looks only at how full each fridge is, never at **when** it is restocked
or **how fast** you are eating. 16% left that resets in 6 hours and 16% left
that has to last 6 days look identical to it. That blind spot is why it got
the example above wrong, and it cannot be fixed by tuning a threshold.

### 2. SURPLUS-EDF (the one `aiu` uses)

> "Of the subscriptions that will still have quota left at their reset, use
> the one that resets **first**."

How it works, per subscription:

1. **Pace** = how much of the weekly quota you've used ÷ how much of the
   week has passed.
2. **Projected leftover** = quota left now − what your pace will use before
   the reset.
3. Label it:
   - **burn**: projected leftover > 5%. Quota will expire unused, so send it
     anything it can do.
   - **on track**: within ±5%. You'll use it about exactly.
   - **conserve**: projected leftover < −5%. It will run dry before the
     reset, so keep it for work only it can do.
4. Rank the burn subscriptions (and promo credits, which simply expire) by
   **earliest reset first**.

"EDF" stands for *Earliest Deadline First*, a scheduling rule from the 1950s
(factory due dates) and 1970s (real-time computing). Supermarkets use the same
idea when they put the milk that expires soonest at the front of the shelf.

**What is proven** (under stated assumptions):

- When any subscription could do the work, "earliest deadline first" wastes
  the least free quota possible, **whatever happens next**. It needs no
  forecast at all.
- When some work fits only one subscription, the rule also uses the pace
  forecast. It loses at most as much as the forecast is wrong, and nothing
  when the forecast is right.
- No rule can do better without some forecast: the problem itself makes
  perfection impossible.
- It never compares "% of Claude" with "% of Gemini". These are fractions of
  different allowances, so comparing them is meaningless.

**Limits**, stated honestly:

- Pace is the average over the current window. It ignores busy afternoons
  and quiet weekends.
- For the first 10% of a window (about 17 h of a week) the pace is not
  trusted, and the subscription is shown as "allure inconnue". It then
  falls back to plain earliest-reset-first.
- It assumes the 5-hour windows rarely block you. When one is about to run
  out, the output says so.
- Task fit stays your call. The ranking says where to send work a
  subscription *can* do.

### 3. SPARE (same core, more machinery)

SPARE asks exactly the same "will it go to waste?" question and gets the same
numbers. It then adds a budgeting system on top:

- **A price list** per kind of task, so free quota goes to the work that
  would be most expensive to pay for.
- **A usage history file**, so pace comes from recent behaviour instead of
  one snapshot. This fixes the "just reset, no pace yet" gap.
- **Confidence labels** (high/medium/low) on every estimate.
- **A batch planner**: give it a pile of work by type, and it computes the
  best split.

It treats all subscriptions with leftovers as equally good ("free is free")
and uses earliest-reset only to break ties. In practice that gives the same
advice as SURPLUS-EDF.

### 4. The exact planner

> "Give me your task list for the week. I'll try every way of assigning
> tasks to subscriptions and return the plan that gets the most done for
> the least money."

It works backwards from the last task to the first, keeping the best choice
at each step. This is Richard Bellman's *dynamic programming* from the 1950s.
It is the most rigorous of the four: the best plan, by construction.

But it needs what `aiu` doesn't have:

- the list of upcoming tasks;
- which models can do each one;
- how much quota each one uses;
- what the paid fallback costs.

It also gets slow quickly as tasks and windows multiply. Without those
inputs, its honest output is "I can't name a best provider".

*Also studied and rejected:* a "capacity over the next H hours" score. It
ignores your pace, counts next week's fresh quota as if it were spare, and
its answer flips depending on the horizon you pick (Gemini at 3 h, Claude at
24 h, Gemini again at 7 days).

## Why SURPLUS-EDF

| | Most headroom | **SURPLUS-EDF** | SPARE | Exact planner |
|---|---|---|---|---|
| Sees reset times | no | **yes** | yes | yes |
| Sees your pace | no | **yes** | yes (better) | n/a (needs task list) |
| Needs beyond the snapshot | nothing | **nothing** | prices, history, task types | task list, costs, fit |
| Proven optimal | no | **yes, for flexible work** | yes, for its model | yes, for its model |
| Fits `aiu` today | yes | **yes, drop-in** | partly | no |

- **It fixes the actual bug.** The old rule ignored time. SURPLUS-EDF is
  built around reset deadlines and your pace.
- **It needs nothing new.** It uses only the snapshot `aiu` already
  collects: no saved state, no config, no extra flags. `aiu` stays a
  stateless, on-demand tool.
- **Its guarantee is strong where it matters.** For everyday "where should
  this go?" work, earliest-deadline-first is optimal regardless of the
  future.
- **SPARE adds a lot for a little.** Its core answer is the same. Its
  extras (prices, history, batch planning) mean new files and flags for
  gains that only appear in edge cases.
- **The exact planner answers a different question.** It plans a known
  batch of work. `aiu` sees quota, not your to-do list.

### Borrowed from the others

- From SPARE: the **"runs dry" warning** in `aiu --alert`, and keeping
  lifetime spend out of the paid overflow list (it's sunk cost).
- From the exact planner's review: one shared answer for every consumer.
  `aiu --json` publishes it as `"advice"`, and `consult auto` and the GNOME
  indicator both read it instead of keeping their own headroom maths.

### Possible next steps

If the "just reset, no pace yet" gap or weekday swings start to matter,
SPARE's **usage history** is the natural next addition. Its **price list**
is worth adding only if it starts to matter which kind of work gets the
free quota.
