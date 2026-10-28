# Document version history vs final text

A document's **final text** and its **version history** are different kinds of evidence. Final text is a single
snapshot. Version history, where an editor keeps one, is a sequence of snapshots or change records, and what it
contains depends on the product.

## Google Docs (what the official help says)

From Google's public help article
[Find what's changed in a file](https://support.google.com/docs/answer/190843):

* Users with **edit access** can open *Version history* to see earlier versions, who edited, and what changed
  (editors are shown with colors).
* Without permission to edit a file you **cannot** see its version history.
* Revisions for a file **may occasionally be merged**, so history is not a complete keystroke-by-keystroke record.
* Editors can **name** versions (the article states a per-document limit).

Google Drive's help
([Check activity & file versions](https://support.google.com/drive/answer/2409045)) notes that version history for
Google Docs, Sheets and Slides is different from Drive file versions.

What follows from this: a version history is a product feature with its own granularity, merging rules and access
controls. It is not a complete log of everything that happened, and what is visible to you depends on your access.

## Browser extensions

Extensions work through a permission model that is separate from the web app. According to Chrome's developer
documentation:

* [Content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts) run in the context
  of web pages and can read details of pages the browser visits, using the standard DOM.
* [Host permissions and match patterns](https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions)
  define which sites an extension may reach; adding or changing them triggers a permission warning, and some
  permissions can be requested later at run time.
* Chrome's guidance is that extensions should request only the sites and APIs they depend on.

What this means for research: what any given extension can obtain depends on the product, the browser, the
permissions the user granted, the account's permissions, and the specific extension. Do **not** assume an extension
can see every historical keystroke or every edit operation. Reading the visible page is not the same as having
access to the product's internal revision data. I could not confirm from the sources above what any particular
extension API exposes about a document's edit history, so treat that as unknown and verify it for the specific
tool you study.

## What this app does and does not do

* It does **not** connect to Google accounts, scrape documents, bypass authentication, or interact with any extension.
* It can **import** a revision-history export or a synthetic dataset that **you are authorized to use**, in the
  documented schema `wpl.revision_history/1` (Import tab). An example file can be saved from the Import tab.
* Imported snapshots are shown as checkpoints. If a file has no events, no events are invented.

## Import schema `wpl.revision_history/1`

```json
{
  "schema": "wpl.revision_history/1",
  "origin": "synthetic",
  "name": "demo dataset",
  "source_description": "Where this came from",
  "authorization": {"attested": true, "statement": "Who authorized this and how"},
  "initial_text": "",
  "events": [
    {"t_ms": 0,   "kind": "insert",  "pos": 0, "inserted": "Hello"},
    {"t_ms": 900, "kind": "replace", "pos": 0, "removed": "Hello", "inserted": "Howdy"}
  ],
  "snapshots": [{"t_ms": 900, "label": "v1", "text": "Howdy"}]
}
```

* `origin` is `synthetic` or `imported`. `imported` **requires** `authorization.attested = true` and a non-empty
  statement.
* Event `kind`: `insert`, `delete`, `replace`, `paste`, `import`, `pause`, `resume`, `checkpoint`, `interruption`,
  `cursor_move`, `external`. Text events need `pos` and the matching `removed`/`inserted` text.
* `t_ms` and `duration_ms` are optional (null = not recorded). Time-based metrics then show *Not recorded*.
* Events must replay consistently against `initial_text`; inconsistent files are rejected whole.
* Limits: 20 MB file, 200,000 events, 5,000 snapshots.
