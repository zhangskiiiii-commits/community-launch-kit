# Campaign output contract

Write UTF-8 `campaign.json` in the campaign output directory. Store generated
images below that directory. The checker uses only the Python standard library
(Python 3.10 or newer). It validates structure and declared gates, not the truth
of webpage evidence or the meaning of generated text. The agent still needs to
read sources, inspect images, and explain access limitations.

## Top-level fields

| Field | Type | Meaning |
| --- | --- | --- |
| `product` | object | `name`, `stage`, `facts` |
| `product.facts` | array | Confirmed product facts as `{ "id": "F1", "text": "..." }`; unique IDs |
| `requested_count` | positive integer | Requested unique demand posts, normally 10 |
| `items` | array | Real candidate records using the contract below |
| `fixture` | boolean, optional | Set `true` only for unmistakably synthetic test/example data |
| `verified_demand_count` | integer, optional | If provided, must equal the computed unique verified needs count |
| `recommended_placement_count` | integer, optional | If provided, must equal the computed ready placement count |

Do not store credentials, account cookies, private group exports or personal
profiles. A concise paraphrase of the problem and a source link are sufficient.
Include provenance for product facts in the surrounding product brief; fact IDs
help find unsupported promises but cannot prove semantic correctness by themselves.

## Each item

Use all object keys below. Unknown facts are `null` or an explicit unknown enum,
never invented. `comment_url`, `published_at`, `copy.title`, `copy.cta`, and
`visual.file` may be `null`.

```json
{
  "id": "R01",
  "platform": "reddit",
  "community": {"name": "", "url": ""},
  "source": {
    "thread_url": "",
    "comment_url": null,
    "title": "",
    "published_at": null,
    "checked_at": "",
    "evidence_status": "inaccessible",
    "thread_state": "unknown",
    "scope": "background",
    "problem_summary": ""
  },
  "product_fit": {"can_help": "", "cannot_help": ""},
  "rules": {
    "url": null,
    "checked_at": null,
    "evidence_status": "inaccessible",
    "summary": "",
    "self_promotion": "unknown",
    "external_links": "unknown",
    "images": "unknown",
    "conditions": "",
    "applies_to_url": null,
    "applies_to_action": "none"
  },
  "recommendation": {
    "status": "needs_check",
    "action": "none",
    "destination_url": null,
    "reason": "",
    "conditions_met": false,
    "manual_steps": [],
    "destination_evidence": {
      "evidence_status": "inaccessible",
      "state": "unknown",
      "checked_at": null
    }
  },
  "copy": {
    "language": "en",
    "title": null,
    "body": "",
    "maker_disclosure": "",
    "cta": null,
    "fact_ids": []
  },
  "visual": {
    "status": "text_only",
    "file": null,
    "prompt": "",
    "alt_text": "",
    "caption": ""
  }
}
```

This is a structural template with empty values, not a valid research record or
an executable example. Do not label the template as verified evidence.

### Enumerations

- `platform`: `reddit`, `facebook`.
- Both `evidence_status` fields: `read`, `snippet`, `inaccessible`.
- `thread_state`: `active`, `locked`, `removed`, `unknown`. Treat archived/non-
  interactive targets as `locked`; do not guess from the age of the thread.
- `source.scope`: `current`, `background`. Always fill this explicitly in real runs.
  `current` means the post is within the user's requested date window. Older posts
  and posts with unknown publication dates are `background`. Missing scope defaults
  to `current` only for compatibility with older files; it is not evidence of recency.
- `self_promotion`: `allowed`, `conditional`, `prohibited`, `unknown`.
- `external_links`, `images`: `allowed`, `prohibited`, `unknown`.
- Recommendation status: `ready`, `needs_check`, `skip`.
- Action: `reply`, `new_post`, `promotion_thread`, `none`.
- Image status: `planned`, `generated`, `text_only`.

Use ISO 8601 timestamps for checks, with a timezone where known. A published date
may remain unknown; a search engine crawl date is not a post's publication date.

### Evidence and placement gates

A verified need requires a specific Reddit/Facebook post permalink, a read body
and a nonempty problem summary. Only `scope: current` contributes to
`verified_needs` and the requested target. Read background posts contribute to
`background_needs` separately and can never be `ready`. The checker deduplicates Reddit post IDs even
when URLs point at different comments, and normalizes common Facebook group
post/permalink variants and tracking parameters. It does not resolve short-link
redirects or detect copied content across different posts: do that during research.

`ready` requires an active, read source; read rule evidence with its URL, summary
and check time; allowed self-promotion (or documented conditions explicitly met);
a destination, concrete action and manual steps; a relevant product benefit;
copyable draft text with language and maker disclosure; and valid `copy.fact_ids`.
The exact `maker_disclosure` must also occur in the body so it survives copying.
A reply destination must belong to the original thread. Rules applying only to a
promotion thread must not be applied to ordinary replies or unrelated new posts.
For every ready record, `rules.applies_to_url` must match the proposed destination
and `rules.applies_to_action` must match the recommended action. `rules.url` remains
the actual rule source; `applies_to_url` identifies the target those rules permit.
Record this applicability only after reading and interpreting the rules.

`recommendation.destination_evidence` records the actual destination's
`evidence_status`, `state`, and `checked_at`. A reply to the original thread may
omit this object (or use `null`) to inherit the source's read/state/check evidence.
If supplied, it must be read, active and timestamped. `new_post` and
`promotion_thread` always require their own explicit read/active/check evidence.
For communities, active means the intended posting surface is open, not merely
that the homepage loads. A new-post destination must be a subreddit or Facebook
group homepage on the declared platform. A promotion-thread destination must be
a specific post on that platform. Access conditions still require human review.

Links in draft/CTA require `external_links: allowed`. Planned or generated images
require `images: allowed`; otherwise choose `text_only` for that placement.
For a new post or promotion thread, explain the distinct destination check in
`reason`. Matching URLs and declared evidence cannot prove actual permission.

`ready` describes a recommended placement, not approval to post and not proof that
all assets are finished. A planned image remains a visible warning. Unknown rules,
unread posts and closed threads cannot be ready. Useful but non-promotional answers
can be retained under `needs_check` or `skip`, with their limited purpose explicit.

### Images

`generated` requires a relative local path, an existing nonempty raster file, an
extension/signature match, and alt text. Supported formats: PNG, JPEG, WebP, GIF,
AVIF. Absolute paths, Windows drive paths, escaping `..` and symlinks that resolve
outside the campaign directory are rejected. Header checks do not replace image
decoding and visual inspection.
`generated_images` counts unique normalized, resolved image paths; reusing one
image in multiple records counts once. Different files with copied bytes are not
automatically detected, so do not present copies as independently created assets.

Use `planned` when only a prompt exists or a generation step is outstanding; keep
`file: null`. Use `text_only` when the placement should have no attached image.
Caption and alt text are plain text. Do not manufacture screenshots, testimonials,
results or features that the product does not have.

## Validation and review files

```text
python scripts/check_campaign.py /path/to/campaign/campaign.json
python scripts/check_campaign.py /path/to/campaign/campaign.json --render review
```

Run the script from the installed skill path, or supply its absolute path.
Relative `--render` paths resolve inside the campaign directory. The render
directory must remain within that campaign directory. It creates `index.html`
and `opportunities.csv` only when neither already exists. Choose a new review
directory on another run; existing files are never overwritten. Symlinks in the
output directory path or either output filename are rejected, including those
pointing inside the campaign. Invalid campaigns are not rendered.

Exit status: `0` means structural validation passed (warnings may still describe
shortfalls or unfinished assets); `1` means validation errors; `2` means input or
output failure. The JSON result includes errors, warnings and computed counts.
Finding fewer than the requested number is a warning, not an excuse to invent
records. Report verified needs separately from ready placements and generated
images, including any unresolved Facebook access limitations.

HTML escapes all user text and restricts source links to HTTP(S). CSV prefixes
spreadsheet-formula-like values with an apostrophe. Images link to the actual
local files. Marked synthetic fixtures render a prominent test-only heading.
