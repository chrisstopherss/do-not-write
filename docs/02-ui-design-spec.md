# UI design: mockups, tradeoffs, spec

Mockups are real HTML, open `mockups/index.html`. Each has a state switcher (Empty / Ready / Running / Paused / Completed / Error) and a theme toggle. Screenshots are in `mockups/screenshots/`. All numbers in them are illustrative and labeled so.

## The three options

**A. Minimal writing studio** (`a-minimal-studio.html`)
- Pros: least intimidating, great for the 90% case (paste, pick duration, start), very low build cost, easy to make accessible.
- Cons: config and analytics are hidden behind other screens; poor for experiment work where you want to see events and settings while it runs.

**B. Research workstation** (`b-research-workstation.html`)
- Pros: everything visible at once (config, editor, timeline, event log, analytics), best fit for the research and reproducibility goals, SIMULATED / COUNTED / HEURISTIC tags make the data provenance rule visible.
- Cons: dense, needs minimum ~1000 px width (right pane collapses below that), highest build cost, more to keep accessible, can feel heavy for casual use.

**C. Modern Windows utility** (`c-windows-utility.html`)
- Pros: looks like it belongs on Windows 11, sidebar maps 1:1 to the spec'd screens (Home, Active, Results, Research, History, Settings), cards scale well, switches with visible labels, easiest to extend later.
- Cons: more chrome and vertical space than A; less information-dense than B.

## Recommendation

**C as the shell, with B's right-hand event/analytics column adopted on the Active Session and Results pages.** C gives navigation that matches the required screens; B's provenance tags and event log are the best idea in any of the three. A's empty-state copy is worth keeping. This is my default pick; it is not user-approved and the layout layer is isolated so switching is cheap. **Waiting for your call before I build the final UI.**

## Navigation
Sidebar: New session, Active session (disabled unless a session exists), Results, Research, History, Settings. Keyboard: Ctrl+1..6 jump to pages, Ctrl+Enter start, Space pause/resume while focus is on the session view, Esc opens the cancel confirmation (never cancels directly).

## States
- **Empty:** dashed editor with Paste and Import actions; Start disabled with a stated reason.
- **Ready:** counts, schedule estimate (including worst-case grace), Start enabled.
- **Running:** live cursor, elapsed/remaining, progress bar with `role=progressbar`, "next event in ~N s" so idle waits are visible, recent events.
- **Paused:** cursor dimmed, clock visibly stopped, Resume is the primary action.
- **Completed:** banner states whether the deadline-vs-sentence rule fired and how much grace was used; diff of original vs final; export, re-run with same seed, compare.
- **Error:** inline `role=alert` banner naming the cause and offering Retry / Change folder; text never lost; running sessions pause safely first.
- **Destructive actions** (cancel session, delete history, overwrite file) always confirm and name what will be lost.

## Accessibility
Every control has a visible label or `aria-label`; groups use `radiogroup`/`role=switch`; focus ring is always visible; no color-only status (tags carry text); contrast targets WCAG AA in both themes; live text updates are not announced per character (the live region would spam screen readers), only milestones (started, paused, completed, error); respects reduced motion; minimum window 900x600.

## Provenance rule (UI-wide)
Every datum is tagged as one of: **COUNTED** (mechanically measured from text), **SIMULATED** (produced by the engine), **HEURISTIC** (an observation with no claim of proof). A persistent "Synthetic output" badge is on every screen that shows output. Nothing in the UI claims to show authentic human behavior, authorship, or AI-detection results.
