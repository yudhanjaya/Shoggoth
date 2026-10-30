# Step 6 picks and text (supplied by the owner, verbatim)

Source: owner's replies in the step 6 session. Sizes are Lemonade catalog download sizes in GB.
Do not add entries or rewrite the text without the owner.

## Small

| Model | Source | Text |
|---|---|---|
| LFM2.5-8B-A1B (5.2) | Lemonade catalog | Competent all-rounder. Terse language, handles tool calls reasonably well, fast - an excellent all-round pick. |
| MiMo-V2.6-Distill-Qwen-9B | Custom: bartowski Q4_K_M (not in Lemonade's catalog). Needs Lemonade 2026.41.1 or newer (llama.cpp b11393+) for correct tool-call parsing | An upgraded version of Qwen 9B. Improved tool calling, managed to pass our code debug tests without issues. A little verbose: tends to go on at length, prone to looping sometimes. |
| Gemma-4-12B-it-MTP (7.8) | Lemonade catalog (`Gemma-4-12B-it-MTP-GGUF`) | Arguably the best conversationalist of the lot at this size; perhaps not the best at tool-calling. Lacks nuanced reasoning and may hallucinate wildly in long-context tasks. |

## Medium

| Model | Source | Text |
|---|---|---|
| Qwen3.6-35B-A3B-MTP (23.8) | Lemonade catalog | The current king of getting shit done on a single GPU. A Mixture-of-Experts model that only activates 3B of its 35B parameters; exceptional tool calling for its size. Very AI-esque conversation, but the default pick. |
| Gemma-4-26B-A4B-it-MTP (18.5) | Lemonade catalog | The alternate to Qwen 3.6. Slightly smaller, but excellent at long-context tasks, multilingual work and reasoning; not as good at Qwen at coding tasks. |
| GLM-4.7-Flash (17.5) | Lemonade catalog | A fast model but a prolific bullshitter. Hallucinates wildly, especially if you give it coding or tool-calling tasks. Run if you prefer its writing style or can't run Qwen. |
| Gemma-4-31B-it-MTP (20.0) | Lemonade catalog | Excels at conversation, writing, and general tasks; is not as good as the Qwen models at tool-calling, agentic work, or coding. Good balance of speed and power; think of it as Gemini's Flash model in a box. |
| Qwen3.8-27B (18.8) | Lemonade catalog | Excellent at coding for its size, but utterly neurotic and slow. Empires have risen and fallen in the time it takes to respond. Use if you're very patient. |

The medium list is final. Muse-Glimmer-30B is not included.

## Large (Strata, default picks)

| Model | Text |
|---|---|
| Qwen3.8 Flash-Next | One of the closest options we have to a frontier model experience at home, thanks to Strata. Blazing fast (often as fast as some of the medium models), roughly comparable to Claude Sonnet 5 for agentic work and code. You need at least 64 GB of RAM and a 12 GB GPU to run this. |
| DeepSeek V4.1 Flash | The darling of token-maxxers. DeepSeek approaches Claude Opus 5 quality (though not quite there), but expect it to be slow on all but the most serious hardware. This one requires some serious hardware - if you need to run it, you need to do some reading. |

The owner wrote "DeepSeek 4.1"; the card uses Strata's own name, "DeepSeek V4.1 Flash".

## Coming soon (greyed out, yellow COMING SOON! label)

| Model | Text |
|---|---|
| LFM3 | Incredibly small, all-purpose models, extremely competent in my testing, punching well above their size. Will be included once Lemonade updates its llama.cpp. |
| GLM 5.3 Flash | Much better model, but much slower. Comparable to Claude Opus 4.8 (which often did better work than Opus 5) and OpenAI GPT Terra 5.6 on many tasks. Will be added once I integrate Project Maya. |

## Rules from the owner

- No tok/s (or any speed figure) on model cards: it can't be predicted on other people's hardware.
- Other kinds (vision, image, speech): no picks given, so they stay as they are.
