# Replay experience and motion rules

The experiential presentation layer formats actual account history. It does not
invent news, supply trading recommendations, change prices, or alter the replay
clock. `core` helpers calculate the observations; `ui/components.py` only renders
the resulting dictionaries.

## Portfolio pulse

After a time advance, the pulse summarizes only newly processed nonbaseline
exchange sessions: their date range and count, the latest session's account
return, the strongest absolute newly revealed daily move and its date, and any
recorded execution costs. A five-session or month-end advance still processes
each intervening session, so an earlier sharp move remains visible even if the
latest session is quiet.

The strongest absolute new daily move determines restrained visual emphasis:

- At least +3%: muted cyan.
- At most −3%: muted coral.
- Otherwise: muted amber, with no motion.

Optional emphasis is a low-opacity border shadow lasting 1.6 seconds per cycle,
with exactly two cycles maximum. It uses no flashing background, confetti,
celebratory reward, repeated timer, or simulated market movement. The caller can
disable it with `animate=False`; the browser's `prefers-reduced-motion: reduce`
preference also disables animation. Native Streamlit text remains readable in
either state. A regular app rerun need not replay the emphasis.

The running app exposes **Animate large portfolio moves** under the sidebar's
**Display preferences**. New time advances may trigger emphasis; ordinary page
changes, report downloads, and allocation edits do not create another simulated
move. The sign belongs to the largest individual daily move, not the net change
over a multi-session advance.

The displayed statement is explicitly a portfolio replay summary, not historical
news. A portfolio's return can differ from market returns because of allocation,
cash, and costs. The component never fetches or infers later observations.

## Three-observation debrief

The native cards summarize the revealed account history through its explicit
as-of date:

1. **Toughest session:** the lowest observed daily account return after costs,
   with its actual date. This may be positive when all observations are gains.
2. **Deepest drawdown:** the largest positive loss below a running equity peak,
   with peak and trough dates. Recovery refers to that deepest episode and is
   measured from its peak to the first subsequent recovery close. Current
   drawdown is shown separately so a recovered earlier episode cannot imply
   that a later decline has also recovered.
3. **Cash, costs, and comparison:** average recorded cash weight, summed recorded
   costs, costs divided by initial capital, and optional benchmark-relative
   cumulative return in percentage points over the matching elapsed window.

The direct cost ratio is not described as a compounded no-cost counterfactual.
Cash earns 0% interest. Undefined and missing values display N/A; a baseline
without a processed return shows an explanatory empty state. These are factual,
deterministic educational observations, not generated explanations of market
causes or advice about which strategy to choose.

The presentation components accept only the restricted dictionaries supplied by
the engine/experience boundary. Synthetic sessions retain the page's explicit
synthetic-source label and do not become historical-performance claims through
an animated presentation.

Source labels also live inside each app figure: **SYNTHETIC DEMO** or
**HISTORICAL DATA** remains visible when Plotly saves a PNG. The grid's unlabeled
CSV shortcut is hidden. The report's four labeled export buttons retain source
and as-of context; Laboratory's comparison CSV records source, retrospective
research mode, scenario, benchmark, and evaluation dates on each row without
turning numeric metrics into text. HTML provenance includes strategy parameters.

## Decision journal

The allocation desk accepts an optional note of up to 500 characters. A note
submitted with a valid manual target is attached to that target's order identifier
and signal close. The input clears after the note is saved. An invalid target or
unfunded ticket does not replace the pending order or silently save a new trade.

**Save observation without trading** records a dated observation without changing
holdings, the pending order, or the replay clock. This also lets a user document a
decision to wait. Journal text is user-authored reasoning, not a verified event or
a generated explanation of market behavior.

Notes are merged into a copied presentation decision log rather than written into
the accounting engine. The report's **Download decision history CSV** and
self-contained HTML include revealed notes; the separate replay CSV remains the
numeric equity history. Pending decisions retain their known reasoning while a
scheduled execution date beyond an interim report's cutoff is redacted.

Journal notes and allocation drafts survive page changes in the same browser
session. Reset or a fresh mission clears dependent journal/draft state; server
restarts do not restore it. Download reports to keep a record. No database,
persistent replay snapshot, or account synchronization is claimed.
