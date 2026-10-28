# Metric definitions

Status values: **ok**, **missing** (data not recorded), **insufficient_data** (recorded but too coarse or too small).
A missing value is never shown as zero. Extractor versions are stored with every result.

## Text metrics (final text)

| Metric | Definition |
|---|---|
| chars | Unicode code points in the final text |
| words | Matches of `\w+(['’]\w+)*` |
| sentences | Split after `. ! ? …` (plus closing quotes/brackets) followed by whitespace, and at blank lines |
| paragraphs | Blocks separated by a blank line |
| sentence_length_words, paragraph_length_words | n, min, max, mean, median, sample stdev (undefined for n < 2) |
| punctuation_counts | Count of each of `. , ; : ! ? - – — ' " ‘ ’ “ ” ( ) [ ] …` |
| immediate_word_repeats | Adjacent identical words (case-insensitive), e.g. "the the" |
| repeated_2grams / 3grams | Word n-grams occurring at least twice (case-insensitive) |
| diff_word_inserts / deletes / replaces | Opcode counts of a word-level SequenceMatcher diff, source text vs final |
| char_similarity_ratio | difflib ratio in [0, 1] between source and final text |

## Process metrics (event data)

| Metric | Definition |
|---|---|
| total_events | All stored events including pauses and checkpoints |
| insertions / deletions / replacements | Events of kind insert (+paste, import) / delete / replace |
| inserted_chars / removed_chars | Sum of inserted / removed text lengths over text events |
| event_size_chars | Distribution of max(len(inserted), len(removed)) per text event, with histogram |
| pause_count, interruption_count, checkpoint_count | Events of that kind |
| revision_events | delete + replace events |
| changed_existing_chars | Characters removed by delete/replace events |
| mean_removed_chars_per_revision | changed_existing_chars / revision_events (missing if none) |
| final_share_from_recorded_events | Fraction of final-text characters created by a recorded insert/replace event; the rest is initial text |
| largest_single_event_share | Largest fraction of final-text characters created by one single event |
| event_intervals_ms | Gaps between consecutive *timestamped text* events (missing if fewer than 2) |
| active_ms / idle_ms | Sum of gaps ≤ / > 2000 ms. The threshold is a convention, not a fact about the writer |
| revisions_per_minute | revision_events / minutes between first and last timestamped text event |
| text_growth | (t_ms, document length) after each timestamped text event |
| chars_per_minute_inserted | Only when ≥ 30 timestamped insert events, all ≤ 3 characters (keystroke grain). Otherwise *insufficient_data* |
| replay_problems | Events that do not apply cleanly to the document, plus ordering problems |

## Final-text research statistics

| Statistic | Definition |
|---|---|
| Type-token ratio | distinct lowercase words / total words (depends strongly on text length) |
| MATTR (window 50) | mean distinct-word fraction over all 50-word windows; needs ≥ 50 words |
| Guiraud's R | types / √tokens |
| Sentence-length CV | stdev / mean of sentence lengths in words |
| Flesch reading ease / FK grade | Standard formulas; syllables estimated by a vowel-group heuristic; English only, approximate |
| Jaccard / cosine / char ratio | Overlap of word sets / cosine of word-count vectors / difflib ratio between two texts |

None of these is a detector. They describe text.

## Evaluation metrics

From a confusion matrix (positive = the labeled class): precision = TP/(TP+FP); recall = TP/(TP+FN);
specificity = TN/(TN+FP); false-positive rate = FP/(FP+TN); false-negative rate = FN/(FN+TP). Undefined ratios
(zero denominator) are shown as undefined, not 0. Intervals are 95% Wilson score intervals. Fewer than 30 samples in a
class triggers a warning. Thresholds are chosen on the train split only.
