# Style-conditioned task collection

The style-only collector composes distinct original material tasks with credited presentation references. The task query stays English and simple. Original source text and attachments are retained unchanged; source-index files are generated metadata, not rewritten factual material.

`prepare_style_batch.py` consumes locally verified `material-packages.json` and `frozen-reference-inputs.json`. It creates a portable plan, `dataset.jsonl` and `tasks.csv`. The 200-task layout has 20 domains with ten tasks each and twenty references with ten tasks each. Every reference receives seven English and three Chinese material tasks across ten different domains. The sampler generates English PPTX output.

The first native Pi user request includes the complete `material.md` body plus four whole reference-page PNGs. The reference manifest separates factual content from design examples and records credits, original page numbers, authorship-evidence limits and usage metadata. Both editable PPTX references and PDF-only references are supported; PDF-only inputs are never labeled editable.

One fresh container and native Pi session are used for each task. Containers have their own workspace and `/tmp`, with read-only runtime/dependency/font mounts. Bash has a forced 180-second maximum. A common system instruction asks the model to finish after export and a structural check; it is not a programmatic one-check cutoff. If the model ends without a PPTX, the configured policy permits at most one automatic reminder in the same recorded session, within the original total turn limit. This handles observed plain-text pseudo-tool calls without rewriting the model's response or hiding the extra harness message. Counts distinguish this automatic continuation from unassisted completion.

The per-worker HTTP proxy stores unchanged request bodies, including native compaction calls. Every archived task records whether the first actual HTTP request contained the complete frozen material body and the four expected image hashes. This proves input availability, not semantic understanding, factual correctness or aesthetic quality. Raw native messages, tools, errors, compactions and timing remain available.

```sh
# Build the original sandbox first as documented in docker/pi-sandbox/README.md.
docker build -f docker/pi-style-input/Dockerfile \
  -t ppt-rl-pi-style-input:20261104 docker/pi-style-input

python tools/prepare_style_batch.py --root /data3/your-style-study \
  --sandbox-image YOUR_IMMUTABLE_LOCAL_IMAGE_ID
python tools/run_style_batch.py --root /data3/your-style-study \
  --plan /data3/your-style-study/plan.json --node /path/to/node --workers 10
```

Commit the runtime before freezing a plan. The runner refuses changed source manifests, an inconsistent runtime commit, overwritten archives, or unexplained active workspaces. `--resume` skips archived attempts using the same plan and code. Creating `ROOT/STOP` prevents further task claims while current tasks finish. After an interrupted active attempt, inspect its record before resuming; do not silently replace it.

Posthoc rendering is separate and never feeds generated previews into the model. The collection is experimental and does not upload to the qualified Hugging Face training-data release. Original reference decks stay local; publisher restrictions and third-party resource credits must remain attached to provenance. A credited author or historical publication date does not establish that every production step was entirely manual.
