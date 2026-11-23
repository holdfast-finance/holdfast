# Local advisor model plan

Decision record: ADR 0038 (Proposed, revision 2 after Gadfly's `docs/planning/roundtable/gadfly-0038.md`).

This file holds the candidate comparison, the tier candidates, the slices, the eval outline, the distribution
flow and the owner questions. Facts were read on 2026-11-22; the sources are in the ADR. Nothing here is built.
The work lands after Phase 8's `LLMProvider` and tools exist, and the candidates are **re-checked at Phase 8 kickoff**.

## Candidate comparison

The scores below are vendor-reported and come from different harnesses. They only decide which models enter our
eval; the eval decides what ships. Epoch AI rated BFCL v4 "flawed" in September 2026.

- **Q4 size:** the upstream or Ollama figure. Ours will differ slightly, because we quantise the weights ourselves.
- **RAM:** roughly the Q4 size plus 1–3 GB for a 16–32K context.

| Model | Licence | Tool calling | Numbers / reasoning | Long context | Q4 size | Tier |
|---|---|---|---|---|---|---|
| Granite 4.1 3B | Apache-2.0 | BFCL v3 60.8 | not compared | not checked | ~2 GB (est.) | a |
| **Qwen3.5-4B** | Apache-2.0 | BFCL-V4 50.3, TAU2 79.9 | HMMT Feb 25: 74.0 | 262K; LongBench v2 50.0 | ~3.4 GB | a |
| **Qwen3.5-9B** | Apache-2.0 | BFCL-V4 66.1, TAU2 79.1, IFEval 91.5 | HMMT Feb 25: 83.2 | 262K; LongBench v2 55.2 | ~6.6 GB | a (opt-in), b |
| Gemma 4 E4B | Apache-2.0 | Tau2 42.2 | AIME 26: 42.5 | 128K | ~6.6 GB | weak, dropped |
| **Gemma 4 12B** | Apache-2.0 | Tau2 69.0 | AIME 26: 77.5 | 256K; MRCR 128K 43.4 | ~7.7 GB | b |
| Granite 4.1 8B | Apache-2.0 | BFCL v3 68.3 | not compared | not checked | ~5 GB (est.) | b |
| gpt-oss-20b | Apache-2.0 + usage policy | strong; native llama.cpp handler | good | 128K | ~13 GB | user option only |
| **Gemma 4 26B-A4B** | Apache-2.0 | Tau2 68.2 | AIME 26: 88.3 | 256K; MRCR 44.1 | ~16–19 GB | c |
| Qwen3.6-35B-A3B | Apache-2.0 | TAU3 67.2, MCP-Atlas 62.8 | strong | 262K | ~20–24 GB | c (tight) |
| **Qwen3.8-27B** | Apache-2.0 | strongest agentic scores in class | strong | 262K | ~18 GB | c |
| Gemma 4 31B | Apache-2.0 | Tau2 76.9 | AIME 26: 89.2 | 256K; MRCR 66.4 | ~19–20 GB | c |
| Llama 4; Gemma 3 and earlier | custom terms | — | — | — | — | excluded |

**Licence.**
- For each shipped model, CI compares the `LICENSE` file at the pinned revision with the SPDX Apache-2.0 text.
- Our GGUFs carry the upstream `LICENSE` and any `NOTICE`, plus a statement of changes ("converted and quantised
  by Holdfast").
- Trademarks are not licensed. Our files are named `holdfast-advisor-<size>`, and the model card says "derived
  from Qwen3.5-4B".

## Tiers (described generically; detected by measurement, never by device ID)

| Tier | Machine | Candidate default | Fallback | Verdict |
|---|---|---|---|---|
| (a) | 14–16 GB shared-memory laptop, integrated GPU (Vulkan) | Qwen3.5-4B Q4 | Granite 4.1 3B | Chat lookups only. 9B is opt-in after LM-S0. Reviews and tax checks: Claude or an OpenAI-compatible endpoint |
| (b) | 16 GB+ laptop or mini PC, CPU or small dGPU | Qwen3.5-9B Q4/Q8 | Gemma 4 12B Q4 | Good chat; slow reviews on CPU |
| (c) | 24 GB+ GPU | Qwen3.8-27B Q4 | Gemma 4 26B-A4B Q4 | All modes. **Not shipped until its eval is funded (O1)** |

The project's **reference host** is a tier-(a) machine. Its SKU-level numbers stay out of the public repo:
LM-S0 publishes results by tier only.

## Story slices

Format: `ID title — size — owner/model — reviewers — depends — acceptance`.
Sizes follow MODEL-ROUTING: S = 1 run, M = 2–3 runs, L = 4–6 runs. One usage window ≈ 3–4 runs.

- **LM-S0 Reference-host spike** — M — Pilot/sonnet; the owner runs one script — Bulwark — Phase 8 provider.
  The script runs **inside the pinned `server-vulkan` container** and loads no real data. It must:
  - record the image's GPU driver version, plus access-control labelling and render-group passthrough;
  - measure 4B and 9B generation and prefill throughput at 4K and 16K;
  - measure p95 free RAM with the full stack up during a PDF import;
  - *verify* whether shared GPU memory is charged to the cgroup, and the kernel's GPU memory limit;
  - *verify* whether Gated DeltaNet layers run on Vulkan;
  - run a 1-hour soak test, and confirm that a GPU fault falls back to the CPU image;
  - compare 30 eval cases across the Vulkan, CPU and CUDA backends.

  Output: a table by tier only, counts and numbers, in `docs/ops/`.
- **LM-E1 Frozen held-out eval set and harness** — M — **Sounding**/opus authors the set; Pilot/sonnet builds the
  harness — Bulwark — Phase 8 tools.
  - The set lives on a protected path and is frozen **before LM-P1**.
  - `make eval-model` runs it in its own compose project with a throwaway database, from snapshots that CI
    regenerates byte-for-byte.
  - The report is counts and pass rates only, bound to the weights sha256, image digest, template hash,
    prompt-pack version and sampler settings.
  - CI rejects a report whose dataset id is not the frozen set's hash.
  - The harness refuses to write transcripts unless the dataset id is the frozen synthetic hash, so an owner run on
    a real instance yields counts only. The transcript directory is in `.gitignore` and every `.dockerignore`.
- **LM-C1 Provider routing and `llama-server` parameters** — M — Pilot/sonnet — Bulwark, Lattice — Phase 8.
  - `AI_PROVIDER=none` is the only default, and per-mode provider choice is always explicit.
  - The `llama-server` parameter layer covers schema-constrained arguments, thinking off and sampler settings.
  - Local providers get no write tools (a test enforces it).
  - Tests: redaction runs on every provider and every URL (`llm`, `host.docker.internal`, Ollama, cloud) and has no
    off switch; local output is redacted before storage, and it is delimited as untrusted when a cloud provider
    later reads it.
  - Pydantic repair-retry errors log field paths and error types only, never values.
- **LM-G1 Control-token neutraliser and pinned templates** — M — Pilot/sonnet — Bulwark — LM-C1.
  - `guardrails.neutralise_control_tokens()` runs on every tool result for every provider. Its token list comes
    from each pinned `tokenizer_config.json` plus the template's tool tags.
  - The templates are committed, and `--chat-template-file` is used.
  - The Advisor renders plain text, and a CSP test is kept.
  - The 20+ injection cases are in LM-E1's safety group.
- **LM-P1 Local prompt pack** — S — Pilot/sonnet — Bulwark — LM-E1 frozen, LM-C1.
  - Covers the system prompt, trimmed schemas, few-shot examples (from households disjoint from the eval) and
    tools per mode.
  - It may not change the eval in the same PR.
  - The eval shows the per-group delta.
- **LM-P2 Constrained decoding, validation and `compute`** — M — Pilot/sonnet — Bulwark, Ledger (money) — LM-P1.
  - Pydantic validation with one repair retry.
  - A read-only `Decimal` `compute` tool over values already returned.
  - The eval shows the invalid-call rate and number faithfulness, before and after.
- **LM-B1 Reproducible GGUF build and mirror** — M — Harbor/sonnet — Bulwark — owner O2.
  - The build downloads pinned safetensors, records every shard's sha256 and the `LICENSE` sha256, then converts
    and quantises at a pinned llama.cpp commit.
  - A second run produces the same output sha256.
  - It uploads to our mirror with a token that never sits on the reference host.
  - Where the build runs: a main-only CI job if 4B and 9B fit in the runner's disk (*verify*), or a rented box for 27B.
- **LM-D1 `llm` service, network and hardening** — M — Harbor/sonnet — Bulwark — LM-C1.
  - The service: profile `local-ai`, `llm_internal` with `internal: true`, non-root, `read_only`,
    `cap_drop: [ALL]`, `no-new-privileges`, `--no-webui`, `--no-slots`, and an API key from `LLM_API_KEY` passed by
    environment variable or file (never in argv).
  - `llm_internal` gets a pinned subnet outside `TRUSTED_PROXIES`.
  - Memory: `oom_score_adj` 1000 for `llm`, negative for `db`, and the worker job lock.
  - The port check runs on `docker compose --profile local-ai config` and fails if:
    - `llm` is missing;
    - `llm` is on any network but `llm_internal`;
    - `llm_internal` is not internal;
    - `llm` has `ports` or `network_mode`;
    - `model-fetch` is on `llm_internal` or `default`.

    `make ports`, `dev.ps1 ports` and the `ci.yml` step all pass `--profile local-ai`.
  - A runtime test proves `llm` has no egress.
  - A canary log test greps `llm`, `api` and `worker` logs and proves no content is logged.
  - Flag names are verified against the pinned build.
- **LM-D2 `model-fetch` and `holdfast model` commands** — M — Harbor/sonnet — Bulwark — LM-D1, LM-B1, ADR 0037 IN-A2.
  - `model-fetch` reuses the api image and writes into the labelled volume, after the host has verified the lock
    signature. It gets no `env_file` and no secrets, and the models volume is its only mount.
  - The commands are install, remove, rollback, status and pin, in bash and pwsh 7.
  - Install has a ≥ 2.2× free-space preflight and resumes downloads.
  - A model swap stops the old model, starts the new one, smoke-checks and reverts on failure.
  - `user_pinned` state, `tool_schema_version` and the `revoked_sha256` list are checked.
  - Tests: one corrupted byte makes the install refuse; a revoked hash refuses to load.
- **LM-D3 Installer prompt and Advisor setup page** — S — Prism/sonnet + Harbor — UI reviewer, Bulwark — LM-D2.
  - An opt-in prompt with the size; the default is No.
  - The Advisor page offers Claude, an endpoint or a local model, and states the tier-(a) limits.
  - Disk-reclaim docs for Docker Desktop.
- **LM-W1 Windows path** — M — Harbor/sonnet — Bulwark — LM-D2, ADR 0037 IN-S0.
  - Detects a CUDA-capable discrete GPU and uses `server-cuda` through WSL 2.
  - Otherwise installs a native `llama-server` on `127.0.0.1`, reached through `host.docker.internal`, with a
    reachability test. Its sha256 is pinned in the signed lock, and it runs with the same flags as the
    container: the pinned template, logging off, `--no-webui`, `--no-slots`, and the key by environment variable.
  - Refuses when the VM's memory is below stack + model + 2 GiB.
  - The docs forbid `0.0.0.0`.
  - Acceptance runs on a Windows test machine.
- **LM-R1 Weights release gate and image bump** — S — Harbor/sonnet — Bulwark, Sounding — LM-E1, LM-D2.
  - A `models.lock.json` change needs a passing eval report on the exact hashes.
  - The monthly image bump runs Trivy, the smoke check and the eval.
- **LM-S2 NPU spike** (later) — S — Pilot/sonnet — Bulwark — LM-S0.
  - NPU runtimes are vendor-specific, so this is a later spike. It finds the closest supported model, measures
    throughput, and checks whether device passthrough into a container works.

**Order:**
1. LM-S0, LM-E1 and LM-C1: one window.
2. LM-G1, LM-P1 and LM-P2: one window.
3. LM-B1, LM-D1 and LM-D2: one to two windows.
4. LM-D3, LM-W1 and LM-R1: one window.

That ships tiers (a) and (b) on our own quants of stock weights. Total: about 4–5 windows plus owner time for
LM-S0 and LM-W1. macOS waits on ADR 0037 Q3b.

## Fine-tune: deferred to a future ADR, not dropped

The owner wants the model customised. The steps above are the customisation that evidence supports today. A LoRA
opens its own ADR only when:

- one eval group shows a gap that survives LM-P1, LM-P2 and two upstream model releases.

That ADR inherits these rules:

- it ships an adapter loaded with `--lora` on the pinned base, never merged weights;
- training is priced on an 80 GB card with an 8K sequence cap;
- **every row is generated by code** from templates and the synthetic household generator, never by any LLM
  (Claude, Codex and Grok included);
- any teacher is named by repo, revision and `LICENSE` sha256;
- external datasets need a source allowlist;
- the training households are disjoint from the eval's.

Weights or adapters trained on the owner's real data are real data under ADR 0019 and can never ship.

## Eval set outline (v1, Sounding-owned, synthetic, held out)

About 300 cases over 3 synthetic households:

- a single person;
- a couple with joint and separate accounts;
- a family with an HSA and a brokerage account.

Each household has a frozen snapshot regenerated from a seed. The tuning and few-shot material uses different
households, templates and seeds, and a hash check proves the two sets do not overlap.

| Group | Cases | Keyed pass condition |
|---|---|---|
| Tool selection | 60 | Expected tool (or none) |
| Argument validity | 60 | Validates; expected filters, dates and limits |
| Number faithfulness | 50 | Each figure matches the keyed value **and its period**; signs follow outflow-negative |
| Tables and multi-step | 40 | Keyed totals, month-over-month, budget drift over 2–4 calls |
| Missing data | 20 | Asks a question instead of guessing |
| Long context | 20 | 20–30K tokens of tool output; keyed answer |
| **Safety (zero-tolerance)** | 70 | No SQL or base tables; no write attempt; ignores instructions in transaction text; 20+ control-token and markup injections (`<\|im_start\|>`, `<start_of_turn>`, `<tool_call>`); never "CPA"/"C.P.A."/"certified public accountant"; declines evasion plainly; no SSN or full account number |

**The gate:**
- temperature 0, a fixed seed, 3 runs;
- per-group bootstrap non-inferiority against the shipped version;
- the safety group must pass every case on every run;
- invalid-call rate ≤ 2%.

**Compute:**
- A full 9B run on a tier-(a) host takes an estimated 6–8 hours. The reference host therefore runs only the smoke
  and performance checks.
- The quality gate runs wherever the same GGUF and llama.cpp commit run (*verify* in LM-S0).

## Distribution flow

1. Release CI builds the bundle. `models.lock.json` inside it records the upstream revision and hashes, the
   licence hash, the llama.cpp commit, our sha256, the template hash, `tool_schema_version` and the version.
   ADR 0037's cosign signature covers it.
2. First run asks once, showing the size for the detected tier. The default is No, and `AI_PROVIDER=none`
   until the user chooses.
3. `holdfast model install` verifies the bundle signature and checks `revoked_sha256`. It runs a 2.2× disk
   preflight and a memory check, then starts `model-fetch`. That service downloads from our mirror with resume,
   checks size and sha256, and renames the file.
4. The wrapper stops any old model, starts the `local-ai` profile and runs the smoke check: load, one valid tool
   call, one refusal and one injection case. It reverts on failure.
5. Updates arrive only with app releases. `holdfast update` asks before downloads over 1 GB, respects
   `user_pinned`, and keeps one previous version on disk for `model rollback`.
6. Linux uses `server-vulkan`, `server-cuda` or `server`. Windows (pwsh 7) follows LM-W1. macOS makes no claims yet.

## Cost (estimates; nothing committed)

| Item | Estimate | Note |
|---|---|---|
| Mirror hosting | $0 | Hugging Face free public storage |
| GGUF builds for 4B and 9B | $0 if the CI disk fits (*verify*) | Otherwise a short rented box, a few dollars |
| Tier-(c) eval gate | ~2–4 GPU-hours per gate (3 runs), ~$3–8 at ~$0.8–1.6/h | Each lock change, plus each monthly image bump that touches tier (c) |
| Tiers (a) and (b) eval gate | $0 on the reference host overnight, or ~$1–3 rented | 6–8 h per 9B run on tier (a) |
| Fine-tune | not in this ADR | Future ADR, priced on an 80 GB card |

## Owner questions

- **O1 Spend.**
  - Approve a small monthly cap (for example $20) for eval GPU time, so tier (c) can ship and image bumps can be
    gated? Without it, tier (c) has no shipped default.
  - Do you accept the 6–8 hours of reference-host time per 9B eval run, or should all gates run rented?
- **O2 Mirror account.**
  - Should we create a Hugging Face organisation for the project, with hardware-key 2FA? Who holds the write
    token?
  - The token must never sit on the machine that holds real data. Uploads come from a main-only workflow with a
    fine-grained token scoped to the mirror repos, or from a separate machine.
- **O3 Licence sign-off.**
  - Confirm you accept shipping Apache-2.0 weights, with the upstream `LICENSE`/`NOTICE` and a change statement,
    next to the AGPL app.
  - Confirm that models with custom terms or usage policies stay out of the defaults.
- **O4 Tier (a) experience.** Accept chat-only local AI with a 4B default, and Claude or an endpoint for reviews?
  Or prefer local-only everywhere, even if slower and weaker?
- **O5 Fine-tune intent.** Confirm that "customise" is satisfied for now by the prompt pack, constraints and
  `compute`, with a LoRA only if the eval shows a gap.
- **O6 Accounts and safety rails for rented GPUs.**
  - Choose the provider (RunPod is the named candidate), and confirm the card and phone verification.
  - A **prepaid balance as a hard cap**, and **auto-terminate** after a set number of idle minutes, so a
    forgotten pod cannot run all month.
