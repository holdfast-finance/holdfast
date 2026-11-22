# 0038. Local advisor model: runtime, supply chain, eval gate and distribution

- Status: **Proposed (revision 2)**
- Date: 2026-11-22
- Deciders: Pilot (AI advisor) drafts; Lattice, Bulwark and Harbor review; Sounding owns the eval set; Keel
  decides. Spend, accounts and the licence sign-off are owner-queue items (Owner questions in the plan).
- Owner request (paraphrased): pick the best open-weight model for the local agent, customise it for
  Holdfast, and have the installer pull it down and wire it to the app.
- **Revision 2 (Gadfly, `docs/planning/roundtable/gadfly-0038.md`, REWORK):** local models get no write tools
  and control tokens are stripped from tool results (B1); we build our own GGUFs from pinned upstream weights
  and harden the container (B2); the small tier defaults to a 4B model with memory guards (B3); training and eval
  rows come from code, not from any LLM (B4); Sounding owns a frozen held-out eval with per-group gates (B5);
  eval reports and runtime logs carry no transcripts (B6). The fine-tune moves to a future ADR (S1), the model
  picks become candidates re-checked at Phase 8 kickoff (S2), and tiers are described generically (S10). The
  finding-to-change table is at the end.
- Read first: PLAN 10 and 20.3 (Phase 8 rows), ADR 0019 (real data stays local), ADR 0021 (private access),
  ADR 0037 (installer, release signing, `CHANNEL`), `.claude/rules/agent.md`.
- Plan, candidate table, slices, eval outline and owner questions: `docs/planning/LOCAL-MODEL-PLAN.md`.
- Nothing here is built. Facts were read on 2026-11-22 from the sources at the end. Vendor benchmark numbers are
  self-reported and use different harnesses, so the eval gate (§6) chooses the models, not the leaderboards.

## Context

- Phase 8 adds `LLMProvider` with `anthropic`, `ollama` and `openai_compatible` backends. `config.py` has a
  single `ai_provider` (default `"anthropic"`), `ollama_base_url` and `openai_compat_base_url`.
- The agent's job is narrow: pick one of about 15 read-only tools, fill typed arguments that validate as JSON,
  read tables of `Decimal` strings and explain them without inventing a figure. It is never called a CPA.
- Tiers (detected by measurement, never by device ID):
  - **(a)** a 14–16 GB shared-memory laptop with an integrated GPU (Vulkan);
  - **(b)** a 16 GB+ laptop or mini PC, CPU only or a small discrete GPU;
  - **(c)** a host with a 24 GB+ GPU.
- The Holdfast stack plus a desktop and browser leaves an *estimated* 6–8 GB for a model on tier (a). This is
  unmeasured; LM-S0 measures it on the project's reference host.
- Facts that shape the design (sources at the end):
  - Every candidate worth keeping is **Apache-2.0** (Qwen3.5, Qwen3.6, Qwen3.8, Gemma 4, Granite 4.1, gpt-oss
    with an added usage policy). Llama 4 and Gemma 3 and earlier carry custom terms and are excluded.
  - Vulkan in llama.cpp works on current RDNA 3.5 iGPUs. ROCm needs a GFX-version override there. AMD's NPU runs
    LLMs on Linux (FastFlowLM 1.0, kernel 7.0+), but only in its own model format.
  - Ollama drops iGPUs unless `OLLAMA_IGPU_ENABLE=1`, has no model signing, and Unsloth reports that custom
    Qwen3.5 GGUFs do not load in it.
  - Docker Desktop gives a container no AMD or Apple GPU. NVIDIA GPUs do get CUDA through WSL 2.
  - Hugging Face serves single files of hundreds of GB with range requests. GitHub Releases caps an asset at 2 GiB.

## Decision

### 1. What this ADR decides, and what it leaves to the eval

This ADR fixes the **mechanism**: the runtime, the supply chain, the network, the lock, the eval gate and the
install flow. The model for each tier is an **output of the eval** (LM-E1) at build time. The candidates in the
plan are a starting list and are **re-checked at Phase 8 kickoff**, because three Qwen generations shipped in six months.

Current candidates:

| Tier | Candidate default | Candidate fallback | Note |
|---|---|---|---|
| (a) | **Qwen3.5-4B** Q4 (~3.4 GB) | Granite 4.1 3B | Qwen3.5-9B Q4 is **opt-in** only, and only after LM-S0 shows ≥ 2 GiB free at p95 during a PDF import. Chat lookups only. Reviews and tax checks should use Claude or an OpenAI-compatible endpoint, and the Advisor page says so |
| (b) | Qwen3.5-9B Q4/Q8 | Gemma 4 12B Q4 | Good chat; slow reviews on CPU-only hosts |
| (c) | Qwen3.8-27B Q4 | Gemma 4 26B-A4B Q4 | **No shipped default until its eval is funded and passed** (owner question O1). Until then, big-host users point `openai_compatible` at their own vLLM or Ollama |

### 2. Behaviour limits for local models (all of them, every tier)

- **No write tools in v1.** Local providers get read-only tools only. No `create_finding`, no
  `propose_tax_tag` and no `add_to_package`. Any later write from a local model needs its own decision, and it
  must go to a pending table. (Gadfly also proposes making `add_to_package` pending for every provider. That is a
  PLAN 10.3 change for Lattice, outside this ADR.)
- **Control tokens are neutralised.** A new `guardrails.neutralise_control_tokens()` runs on every tool result,
  for every provider, next to `redact()`. Its token list comes from each pinned model's `tokenizer_config.json`,
  plus the tool-call tags of each template. There is a test per model family.
- **The chat template is ours.** `llama-server` runs with `--chat-template-file` pointing at a template
  committed in the repo, and the lock pins its hash. The template embedded in the GGUF is never used.
- **Plain-text output.** The Advisor renders local-model output as plain text: no links, no images. A test keeps
  the CSP's `img-src 'self' data:`.
- **Redaction has no off switch.** `redact()` runs on payloads to `openai_compatible` when it points at `llm`.
  There is no setting to disable it for local providers, and a test proves both. Local output is also redacted
  before it is stored in a conversation that a cloud provider may read later under per-mode routing.
- Never "CPA": the model card calls it a tax-prep and planning assistant that can be wrong.

### 3. Runtime and supply chain

- **Runtime:** llama.cpp `llama-server` (MIT). We use the official images: `server-vulkan`, `server` (CPU) and
  `server-cuda` when an NVIDIA GPU is detected. Each is pinned by digest. The app reaches it through the
  `openai_compatible` provider, plus a small `llama-server` parameter layer (§7).
- **We build our own GGUFs.** A pinned, reproducible job (LM-B1):
  1. downloads upstream safetensors at a pinned revision and records every shard's sha256 and the `LICENSE`
     file's sha256;
  2. converts and quantises them at a pinned llama.cpp commit (text weights only, no vision projector);
  3. publishes the output to **our own Hugging Face mirror** (account: owner question O2).

  The lock records the upstream revision, the shard hashes, the licence hash, the llama.cpp commit and our output
  sha256. Third-party quants (such as Unsloth's) are not used.
- **Licence check in CI:** for each pinned revision, CI fetches `LICENSE` and compares it with the SPDX
  Apache-2.0 text.
- **Container hardening:**
  - `user` non-root, `read_only: true`, `cap_drop: [ALL]` and `no-new-privileges`;
  - `--no-webui`, `--no-slots` and no slot-save path;
  - `--api-key` from a new `.env` secret (`LLM_API_KEY`, generated by `make keys`, listed in `.env.example`
    without a value);
  - weights mounted read-only.

  LM-D1 verifies the exact flag names against the pinned build.
- **Bump cadence:** the image is bumped monthly, and within a week of an upstream security fix. Each bump runs
  Trivy, the smoke check and the eval gate. The bumped digest lands in a normal PR.
- **Logs carry no content.** `llama-server` runs with prompt and response logging off (warn level). Its compose
  logging uses a tiny rotation. A canary test greps the container logs after an eval run for a planted string.

### 4. Compose, network, memory and opt-in

- **Service and network.** A new service `llm` sits under a compose profile `local-ai`, which starts only if
  the user opts in. A new network `llm_internal` has `internal: true`. Only `llm`, `api` and `worker` join it.
  `llm` has no `ports`, no `network_mode` and no other network.
- **Proving hard rule 12.** `scripts/check-compose-ports.py` is extended to check that `llm` is only on
  `llm_internal`, that the network is internal, and that there are no `ports` and no `network_mode` (LM-D1). A
  runtime test proves that `llm` cannot connect out.
- **Fetching weights.** A one-shot `model-fetch` service reuses the Holdfast api image with a fetch command, so
  there is no new third-party image. It runs on demand on its own egress-only network. Before it starts, the host
  wrapper verifies the lock's signature and passes it the expected sha256. It writes a `.part` file straight
  into the labelled `holdfast_models` volume, resumes with range requests, checks the size and sha256, then
  renames the file. A mismatch deletes the file and fails. `llm` never downloads anything.
- **The volume.** `holdfast_models` carries ADR 0037's install-id label, so `remove` and `--purge` see it. It is
  excluded from backups because it can be re-downloaded. A free-space preflight requires ≥ 2.2× the model size.
  Disk-reclaim steps are documented for Docker Desktop, whose VM disk does not shrink by itself.
- **Memory:**
  - `llm` gets `oom_score_adj: 1000` and `db` gets a negative value, so the OOM killer picks the model, not Postgres;
  - `mem_limit` is set from the tier;
  - the worker holds a job lock, so a local-LLM job never overlaps a PDF import;
  - context is 16K on tier (a) and 32K elsewhere, with one slot on tiers (a) and (b).

  *Verify in LM-S0:* on an iGPU, Vulkan buffers sit in GTT and may not be charged to the container's cgroup, and
  the TTM limit may be below what the model needs.
- **Swapping a model:** stop the old model, start the new one, smoke-check, and revert on failure. Disk holds
  both versions; RAM never does.
- **Opt-in and defaults:**
  - The model is opt-in and never the default. First run asks once, shows the size, and the default answer is No.
  - `AI_PROVIDER` gains `none`, and `none` is the **only** default. A cloud provider needs an explicit choice and
    is never inferred from a key being present.
  - The app works fully with no AI; the Advisor page shows the setup choices.

### 5. Lock, signing, updates and revocation

- **The lock.** Each app release carries `models.lock.json`. For each model it records:
  - the upstream repo, revision, shard hashes and licence hash;
  - the llama.cpp commit and our output file, size and sha256;
  - the template hash, `tool_schema_version`, our version (for example `holdfast-advisor-4b-v1.0.0`) and the
    minimum app version.

  The lock is inside the release bundle, so ADR 0037's cosign signature covers every pin.
- **Active model state.** A state file records the active model and a `user_pinned` flag. A smoke check runs on
  any change of image, model or template. `holdfast update` respects a user pin (for example after
  `model rollback`) and never silently reinstalls a model the user rolled back from. It asks before any download
  over 1 GB.
- **Revocation.** Each release's signed `CHANNEL` carries a `revoked_sha256` list. `holdfast update` and
  `holdfast model install` refuse to load a listed file and offer the previous good version. There is no
  background check (no telemetry, ADR 0018 item 5).

### 6. Eval gate (owned by Sounding)

- **Ownership.** Sounding authors and freezes a **held-out** eval set before the prompt-pack work (LM-P1)
  starts. It lives on a protected path, and a PR may not change both the eval set and the prompt pack.
- **No overlap with tuning data.** The eval set's households, templates and seeds are disjoint from anything
  used for tuning or few-shot examples, and a hash check proves it.
- **Scoring:**
  - answers are scored against keyed expected answers, including the period a figure belongs to;
  - each group is gated separately: temperature 0, a fixed seed, 3 runs, and bootstrap non-inferiority against
    the shipped version;
  - **safety cases are zero-tolerance**, and the safety group includes 20 or more control-token and markup
    injection cases;
  - banned-claim checks cover the variants ("CPA", "C.P.A.", "certified public accountant").
- **The report.** It binds the weights sha256, the image digest, the template hash, the prompt-pack version and
  the sampler settings. It carries **counts and pass rates only**, with no transcript fields. Debug transcripts
  for the synthetic set stay in a git-ignored local directory. CI rejects a report whose dataset id is not the
  hash of the frozen synthetic set.
- **Where it runs.** Evals run in their own compose project with a throwaway database, loaded from snapshots that
  CI regenerates byte-for-byte from the seed. They never touch a live instance.
- **Where the compute comes from.** The quality gate depends on the GGUF and the llama.cpp commit, not on the
  machine, so it can run on any host. *Verify in LM-S0* by comparing 30 cases across the Vulkan, CPU and CUDA
  backends. The reference host runs only the smoke and performance checks. A full 9B run on a tier-(a) host takes
  an estimated 6–8 hours. Tier-(c) gates need a rented GPU, which is in O1.

### 7. Provider routing (the code this needs)

The local path is not "no new code". Phase 8 needs:

- **per-mode provider selection**, for example chat on `llm` and reviews on Claude, each an explicit user
  choice;
- **`llama-server` parameters**: schema-constrained tool arguments, thinking off, and the sampler settings;
- **the read-only `compute` tool**, which evaluates sums and ratios in `Decimal` over values already returned.

These are slice LM-C1 in the plan.

### 8. Customisation: in this ADR, and deferred

- **The owner wants the model customised.** This ADR covers the steps that are cheap, reversible and tied to the
  eval:
  1. the eval gate;
  2. a local prompt pack (short system prompt, trimmed schemas, few-shot examples, tools per mode);
  3. schema-constrained decoding with Pydantic validation and one repair retry;
  4. the `compute` tool, so the model copies numbers rather than doing arithmetic.
- **A LoRA fine-tune is deferred, not dropped.** It opens as a **separate ADR** only when one eval group shows a
  measured gap that survives the steps above and two upstream model releases. If it happens, that ADR inherits
  these rules:
  - it ships a small adapter loaded with `--lora` on the pinned base, never merged weights;
  - training is priced on an 80 GB card with an 8K sequence cap;
  - **every training row is generated by code** from templates and the synthetic household generator, never by
    an LLM (Claude, Codex or Grok included);
  - any teacher model must be named by repo, revision and `LICENSE` sha256;
  - external datasets need a source allowlist, because many function-calling sets are non-commercial.
- **Real data.** Weights or adapters trained or tuned on the owner's real statements, transactions or exports
  **are real data under ADR 0019**. They could never leave the machine, never ship and never be uploaded. No such
  run exists in any plan. The owner may run the eval locally against their own instance, but those results are
  reported as counts only and never become rows.

### 9. Windows and macOS

- **Windows:**
  - The container model is **off by default**. A CPU-only container takes about a minute per turn.
  - With an NVIDIA GPU, the installer detects it and uses `server-cuda` through WSL 2.
  - Otherwise the default is a native `llama-server` bound to `127.0.0.1` and reached through
    `host.docker.internal`. The binary is pinned by sha256 from llama.cpp's GitHub Releases and uses the same
    template and flags. A reachability test confirms it.
  - `holdfast model install` refuses when the Docker VM's memory (from `docker info`) is below the stack plus
    the model plus 2 GiB.
  - The docs forbid binding Ollama or LM Studio to `0.0.0.0`, which would break hard rule 12.
- **macOS:** no claims until ADR 0037 Q3b (a Mac to test on) is answered.

### 10. New external services (hard rule 9)

| Service | Used for | Data it sees |
|---|---|---|
| Hugging Face Hub, including its Xet/CDN download hosts | Upstream safetensors (Qwen, Google, IBM repos) and our GGUF mirror | Public weights; download requests from users' IPs |
| `ghcr.io/ggml-org/llama.cpp` images (`server`, `server-vulkan`, `server-cuda`; the CUDA one embeds NVIDIA's CUDA base) | Runtime | Image pull only |
| NVIDIA Container Toolkit (host, user-installed) | CUDA passthrough on NVIDIA hosts | None |
| llama.cpp GitHub Releases | Native Windows `llama-server` binary | Download request only |
| A GPU rental (RunPod is the named candidate; owner decides in O6) | Tier-(c) eval runs | Synthetic eval set, public weights |

`model-fetch` reuses our own api image. Training services belong to the future fine-tune ADR.

## Consequences

- Good: a private AI path that never leaves the host, with weights we built, pinned, signed and can revoke, and
  zero cost to users who skip it.
- Good: a local model cannot write anything in v1, so a prompt injection can at worst produce a wrong answer
  in chat.
- Cost: one service, a fetch job, a network, a template per model family, a monthly image bump and an eval
  gate that needs GPU time for tier (c).
- Cost: weights are 3–19 GB, so model updates are explicit and rare.
- Risk: tier (a) gets a modest, chat-only agent. The installer and the Advisor page say so.
- Risk: upstream architecture churn (Gated DeltaNet in Qwen3.5+) can break a backend. The image is pinned, and the
  smoke check runs before any switch.
- Risk: Vulkan compute on a display iGPU can hang the desktop. LM-S0 runs a soak test, and the installer falls back
  to the CPU image on a GPU fault.
- Reversible: dropping the `local-ai` profile and the lock entries removes all of it.

## Alternatives considered

- **Bundled Ollama with `ollama pull`.** Simplest, but no signatures, iGPUs off by default, and custom Qwen3.5
  GGUF problems. Kept only as a user-managed provider.
- **Third-party quants (Unsloth and others).** We would be trusting whoever made the GGUF on first use.
  Rejected (B2).
- **GitHub Releases for weights.** The 2 GiB limit per asset forces split files. Rejected as the primary host.
- **Llama 4 and older Gemma.** Custom licences. Rejected. **gpt-oss-20b** stays a user option: 13–16 GB leaves
  no room on 16 GB hosts, and it adds a usage policy.
- **Weights inside the Docker image.** Multi-GB images, no opt-in. Rejected.
- **Fine-tuning now.** No evidence yet that prompt, constraints and `compute` leave a gap. Deferred (S1).
- **The NPU path.** Different model format. Spike LM-S2.

## Gadfly response (revision 2)

| Finding | Change |
|---|---|
| B1 role/tool forgery | §2: no write tools for local models in v1, `neutralise_control_tokens()`, pinned template, plain-text rendering; 20+ injection evals (§6) |
| B2 supply chain | §3: own GGUFs from pinned safetensors and shard hashes, own mirror, licence check, hardened container, bump cadence; report binds image digest (§6) |
| B3 memory | §1: tier (a) defaults to 4B, 9B opt-in after LM-S0; §4: OOM priority, job lock, stop-then-start swap, GTT *verify*; §9: Windows VM memory check |
| B4 data licence | §8: rows from code only, no LLM; teacher pinned by `LICENSE` hash; "or larger" removed; CI licence check (§3); dataset allowlist |
| B5 eval gate | §6: Sounding-owned frozen held-out set, disjoint by hash, keyed answers, per-group 3-run non-inferiority, zero-tolerance safety |
| B6 real-data leaks | §3: no content logging and a canary test; §6: counts-only report, dataset-id check, throwaway compose project, reproducible snapshots |
| S1 fine-tune | §8: deferred to a future ADR with a trigger; adapter not merged weights |
| S2 stale picks | §1: picks are candidates, re-checked at Phase 8 kickoff |
| S3 provider code | §4 `none` only default; §7 and slice LM-C1 |
| S4 disk/volume | §4: `model-fetch` service, 2.2× preflight, label, backup exclusion, reclaim docs |
| S5 update/rollback | §5: `tool_schema_version`, smoke on any change, `user_pinned`, signed `revoked_sha256` |
| S6 Windows/macOS | §9: native loopback runtime, CUDA detection, `0.0.0.0` banned, no macOS claims |
| S7 rule 9 table | §10 completed |
| S8 rule 12 proof | §4: extended compose check plus runtime egress test (LM-D1) |
| S9 owner costs | Plan O1 (eval compute), O6 (accounts, prepaid cap, auto-terminate), host time stated; HF token never on the real-data host (O2) |
| S10 privacy | Tiers generic, "reference host", detection by measurement |
| S11 iGPU stability | LM-S0 in-container, records Mesa version, 1-hour soak, SELinux/render-group checks; CPU fallback on GPU fault |
| S12 redaction | §2: no off switch, test for `llm`, redact local output before storage |

## Sources (read 2026-11-22)

- Qwen3.5-9B: https://huggingface.co/Qwen/Qwen3.5-9B ; Qwen3.6-35B-A3B: https://huggingface.co/Qwen/Qwen3.6-35B-A3B
- Qwen3.8-27B: https://huggingface.co/Qwen/Qwen3.8-27B , https://github.com/QwenLM/Qwen3.8 ; Qwen org: https://huggingface.co/Qwen
- Gemma 4 and its licence change: https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/ ,
  https://opensource.googleblog.com/2026/03/gemma-4-expanding-the-gemmaverse-with-apache-20.html ,
  https://huggingface.co/google/gemma-4-12B-it , https://huggingface.co/google/gemma-4-26B-A4B-it
  (the pinned `LICENSE` file is the authority, checked in CI per §3)
- gpt-oss: https://openai.com/index/introducing-gpt-oss/ , https://huggingface.co/openai/gpt-oss-20b
- Granite 4.1: https://huggingface.co/ibm-granite/granite-4.1-8b , https://research.ibm.com/blog/granite-4-1-ai-foundation-models
- Mistral Small 4: https://mistral.ai/news/mistral-small-4/ ; Mistral 3: https://mistral.ai/news/mistral-3
- BFCL: https://gorilla.cs.berkeley.edu/leaderboard.html ; Epoch AI review:
  https://epoch.ai/benchmarks/berkeley-function-calling-leaderboard/review
- Ollama library: https://ollama.com/library/qwen3.5 , https://ollama.com/library/qwen3.8 , https://ollama.com/library/gemma4
- Ollama releases and GPU docs: https://github.com/ollama/ollama/releases , https://docs.ollama.com/gpu
- Ollama RDNA 3.5 iGPU issues: https://github.com/ollama/ollama/issues/14562 , https://github.com/ollama/ollama/issues/15601
- ROCm builds for RDNA 3.5 iGPUs: https://github.com/lemonade-sdk/llamacpp-rocm
- NPU on Linux: https://phoronix.com/news/FastFlowLM-1.0 , https://lemonade-server.ai/flm_npu_linux.html
- llama.cpp images and function calling: https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md ,
  https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md
- Unsloth Qwen3.5 notes: https://unsloth.ai/docs/models/qwen3.5.md , https://unsloth.ai/docs/de/modelle/qwen3.5/fine-tune
- GitHub release limits: https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- Hugging Face storage limits: https://huggingface.co/docs/hub/storage-limits
- GPU rental prices: https://www.runpod.io/pricing , https://getdeploying.com/gpus/nvidia-l40s
- Critique: `docs/planning/roundtable/gadfly-0038.md`
