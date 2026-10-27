# Scope decisions

## External typing into other applications: NOT being built

The brief asks for an optional feature that types the simulated output (with timing spread over hours, typos, and corrections) into other apps such as a word processor. I'm not building that part. Reasoning:

- Spread over time with realistic mistakes and fixes, typed into Google Docs / Word / an LMS box, that output produces a revision history that looks like someone wrote it by hand. That is the artifact people use to pass off pasted or generated text as their own writing in settings that check for exactly that (coursework, assessments, hiring tasks, some publishing workflows).
- The brief's safeguards (opt-in toggle, emergency stop, "never claim it's human") are good engineering, but they don't change what the output looks like to whoever reads the revision history later. The person being deceived never sees the opt-in dialog.
- Everything else in the brief works without it: in-app simulation, seeded reproducibility, analytics, exports, comparison reports, and a local test window.

What stays: the in-app progressive-writing session, the mistake engine, timeline, analysis modules, and exports, all clearly labeled synthetic. What is cut: sending synthetic keystrokes to any process outside this app. The "safe local test window" can still exist as an in-app test pane that receives simulated events, which is useful for testing the event pipeline.

If there's a concrete legitimate case I'm missing (for example, generating keystroke-style datasets to test a detector you own, or demo recordings), say so. Most of those need the event log and exports, not injection into third-party apps, and I can make those richer. If the real goal is the revision-history-looks-human outcome, I won't build toward that.

## Also worth being straight about

- "Cybersecurity competition practice" is vague. The legitimate security angle here is generating synthetic data for testing detectors, which the event/CSV/JSON export covers.
- The brief says not to claim heuristics prove authorship. Agreed, and the analysis modules will say "observation" not "verdict". Note that works against the tool's own likely use too: if a revision history can be faked in an afternoon, it is weak evidence, and the people relying on it should know that.
