# Research guide: comparing writing workflows

This module helps you run **controlled, labeled experiments** on how different workflows look in process data
(event logs, revision snapshots, timing). It is a research tool. It collects measurable observations; it does not
decide who or what wrote a text.

## Ground rules

* Use synthetic text, or text you are explicitly authorized to use. Do not collect other people's writing data
  without their authorization.
* Every record carries a data origin: **SIMULATED** (made by this app's engine), **SYNTHETIC** (artificial data
  supplied to the app), **IMPORTED** (user-authorized external data) or **CAPTURED** (recorded by the app from input in
  its own editor; this version never creates captured data).
* Conditions A-E are *treatments*, not models of real writers or AI systems. A real person, tool, or editor can produce
  any of these patterns, and many will produce none of them cleanly.
* This app does not optimize traces to look like any particular writer, and it must not be used to forge a document's
  revision history or to present automated work as composed by a person.

## The five conditions

| Code | Treatment | What the event log contains |
|---|---|---|
| A | Incremental composition | Many small word-sized insertions, simulated pauses, optional simulated mistakes and corrections |
| B | Whole-text reproduction | The existing text entered sentence by sentence at irregular intervals, no mistakes |
| C | Automated insertion | One (or a few) very large insertion events at the start |
| D | Revision-heavy editing | Sentence-level insertion followed by a seeded sequence of replacements, deletions and additions, with checkpoints |
| E | Mixed | Incremental entry, then a bulk insertion, then sentence entry, then revisions |

## Things to think about when you compare them

**Incremental entry vs inserting existing text.** Event *counts* and *sizes* differ enormously (condition C has one event
of the whole text; condition A has hundreds). But the final text can be identical, which is why final-text statistics
cannot separate them.

**Deletions, replacements, revisions.** Revision counts depend on the treatment's parameters, not on a fixed property of
a workflow. A tidy writer, a careful editor and an automated tool can all produce few revisions.

**Timing and event granularity.** Timing metrics are only meaningful when events were recorded with timestamps at a
suitable grain. If an export has per-sentence events, no typing speed can be derived from it. This app shows
*Not recorded* or *Insufficient data* instead of guessing.

**Checkpoint frequency.** Checkpoints only show document state at the moments they were taken. Short-lived text
between checkpoints is invisible. Merged or coarse revisions lose detail.

**Text length changes.** Growth curves depend on granularity and on how much was inserted at once.

**Copy/paste.** A paste is a bulk insertion. Writers paste their own earlier notes, quotes, and drafts all the time.
A large insertion is consistent with many workflows.

**Mixed human and automated workflows.** Real work mixes drafting, pasting, tool assistance and editing. A two-class
label (human / automated) does not describe such documents.

**Missing data and limits of observation.** What you can see depends on what was recorded. Absence of an event in the
data is not evidence the action did not happen.

**False positives and false negatives.** Any rule over these features will misclassify real documents in both
directions. The Evaluation tab shows how to measure this on labeled data, with intervals and small-sample warnings.
Performance on synthetic data you generated yourself does not transfer to real documents.

**Correlation is not proof.** A process pattern may be *consistent with* a workflow without *uniquely identifying* it.

## Suggested experiments

1. *Insertion vs incremental*: run template "Insertion vs incremental entry". Look at event counts, size
   distributions, and `largest_single_event_share`. Then ask: which real, innocent workflows would also produce a
   large share?
2. *Revision sweep*: run the revision-frequency template and check that your revision metric changes as parameters
   change, and where it saturates.
3. *Checkpoint sweep*: see how checkpoint interval changes what the revision explorer can show.
4. *Evaluation*: run the evaluation on the example experiment, then change the split seed several times and watch how
   unstable the estimates are with 25 samples.

## Writing rhythm profiles and breaks

Session configuration and the Rhythm tab let you vary **timing assumptions** and see how they change measurable
process statistics. Presets: *Regular* (short pauses, occasional brief breaks, moderate revisions), *Reflective*
(more frequent pauses, longer thinking breaks up to five minutes, more revisions), *Extended Breaks* (bursts of writing
separated by long idle gaps of one to five minutes) and *Custom*.

What these are: **experimental parameters** (pause length range, probability of a short pause per word, probability of
a break per sentence boundary, break length range up to five minutes, revision frequency, how writing speed varies over
the text). Every pause and break is generated by the engine from a saved seed, is stored with its start and end, and
is labeled SIMULATED.

What they are not: models of how any person writes. A break length says nothing about whether the break was "natural".
No preset is meant to look human, to pass any detector, or to be hard to detect, and none was tuned against any
third-party tool. Do not use them for that, and do not read the results that way.

How the time budget works: the selected duration includes the breaks. Break and pause lengths are absolute; the
engine fits the *active writing time* into what is left. If the breaks plus the minimum writing time cannot fit, the app
says so and offers two alternatives (a longer duration, or less total break time). With *Fit writing speed* turned off
you can set a fixed pace; then the plan may not finish, and the deadline rule applies: the sentence in progress is
finished if that stays within the grace period (default 5 minutes, hard cap 10), otherwise output stops at the
previous sentence boundary. Sessions record whether they completed normally, within grace, or were stopped by the deadline.

What the Rhythm tab reports, per session and per profile: active vs idle time (simulated clock), number of breaks,
average and longest break, distributions of pause lengths and writing-interval lengths, text added per active
interval, and revisions per active minute just before vs just after breaks. Sessions whose pauses were not recorded show
*Not recorded*, not zero.

Questions worth asking of the data:
* How much of the change in idle time comes from many short pauses vs few long breaks?
* Do final text length and word counts change across profiles? (With fitted time they barely do: rhythm changes the
  process record, not the text.)
* How large would a difference have to be, with the number of runs you have, before you would trust it? With a handful of
  runs per profile the answer is: larger than the app can show. The comparison table warns when there are fewer than 30.
* Which of these statistics would also be produced by an ordinary person who got interrupted, or by a script with a
  configured delay? Most of them.

Real vs simulated: in this version the only real application telemetry is your own Pause, Resume and Cancel presses
(session-clock time and phase), stored separately from the simulated events and shown as markers on timelines.
