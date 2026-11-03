# Paired style-reference trials

Use an original content task twice: once without a reference and once with an
editable target-style PPTX. A PDF rendered from that exact reference PPTX can be
supplied as a source visual. The base query, original content files, model,
sampling parameters, per-request seed schedule and absolute working directory
are held constant within the pair. The template and its short style-only query
suffix are the treatment. The common system prompt already separates visual
references from factual sources.

Prepare a separate experiment directory. A frozen `plan.json` has this structure
(replace the example hashes with the actual SHA-256 values):

```json
{
  "model": "qwen3.8-flash-next-unsloth-q8_0",
  "endpoint": "http://127.0.0.1:8099",
  "max_turns": 80,
  "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "max_tokens": 32768, "thinking_budget": 8192},
  "pairs": [{
    "pair_id": "pair01",
    "task_id": "original-task-id",
    "query": "Create a PPTX presentation in English based on the provided materials.",
    "materials_dir": "inputs/original-task-id",
    "content_files": [{"file": "material.md", "sha256": "ACTUAL_CONTENT_SHA256"}],
    "style_template": {"file": "templates/example/target-template.pptx", "sha256": "ACTUAL_PPTX_SHA256"},
    "style_pdf": {"file": "templates/example/target-template.pdf", "sha256": "ACTUAL_PDF_SHA256"},
    "base_seed": 120100,
    "order": ["baseline", "template"]
  }]
}
```

Counterbalance the condition order across tasks before starting, rather than
choosing it from observed outcomes. Keep the original source URLs, per-template
licenses, credits and conversion commands alongside each reference. Label any
neutral placeholder text added to empty official templates as a derived probe,
not as official slide content.

From a committed, clean checkout with the Pi dependencies installed:

```bash
python tools/run_style_ab.py --root /path/to/experiment --plan /path/to/experiment/plan.json \
  --node /path/to/node --parallel-pairs 4
```

Every declared condition is attempted once; failed generations are archived and
not replaced. `status.json` and `experiment-events.jsonl` record progress. Each
pair uses `active/<pair_id>/work` for both conditions, then moves the whole run
to `runs/<pair_id>/<condition>`. Original native logs are not rewritten, so
their absolute paths describe the historical active directory; use the known
archived relative path and verify the result's PPTX hash. An existing active
attempt requires inspection; `--resume` only skips already archived attempts
under the identical plan and code commit.

Render generated decks only after inference exits. These post-hoc screenshots
are not model feedback. Distinguish a supplied reference from a reference
actually inspected by the model: native read audits establish visual exposure;
text/XML inspection may instead be visible in bash tool calls. Missing exposure
must be reported. Matching seeds do not imply identical tokens under different
inputs or deterministic concurrent serving, and native compaction is separately
recorded. Ten paired examples with one draw per condition are an exploratory
check, not an estimate of statistically significant improvement.

This experiment has its own output directory and never modifies or uploads to
the pinned qualified SFT release. Review slides for both style following and
content preservation; template resemblance alone does not establish quality.
