# Project conventions

<!-- The agent reads this at the start of every session. Keep it short and current.
     Graded: does it reflect how the team actually works? -->

## What this repository is
One paragraph. Link to the current `spec.md`.

## Commands
```
# build
# test
# run
# lint
```

## Conventions
- Language and style rules the agent must follow.
- Where tests live and how they are named.
- Branch and PR naming.

## Working rules

For an introductory lab, follow its explicitly assigned stages; the full chain below applies to major projects. Week 1 uses its own minimal repository.

- Write or update `intent/` and `spec.md` before code. Get `plan.md` approved before implementing.
- One feature per branch and pull request. Never push to `main` directly.
- Never commit `.env` or `.claude/settings.local.json`.
- This is the Week 5 introductory lab. Stages assigned: intent, spec, plan, and tests. No branches or pull requests yet. Commit to main.
- Libraries and build tools are allowed. Name each dependency in spec.md under Dependencies, with the reason, before installing it; a hook blocks installs that spec.md does not list.
- images/ and images/expected.json are the answer key. Never edit them. If a test disagrees with the key, say so and stop.
- Tests live in tests/ (Python) or src/test (Java). Tests that call a model are marked and are skipped when no API key is set.
- Week 6: the image-set lab lives in week06/. Stages assigned: intent, spec,
  plan, tests, and (in the team repository only) a reviewed pull request.
  Commit to main here; branches and PRs are for the team repository.
- Image generation and vision calls are billed. One generation per prompt,
  one critique per image, fixed seeds. Never loop over the model inside a test.
- The critique is model output. The code compares it with the requirements;
  it does not trust it.

## Common mistakes
Things the agent got wrong before and must not repeat. Add to this list as they happen.
