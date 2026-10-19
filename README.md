# Artifact-chain template

Starting point for major project submissions in CPSC 415 (AI Integration, Trinity College). Click **Use this template** on GitHub to create your own repository from it. Do not fork.

The course follows Anthropic's [AI-Native SDLC Playbook](https://claude.com/blog/the-ai-native-sdlc-playbook): every stage of the work leaves a short, version-controlled artifact. The agent writes most of the code. You decide what gets built, steer, verify, and explain every choice. These files are how you prove you understood what the agent built.

## Early labs

Week 1 uses the minimal repository described in the course handout. Later introductory labs complete only the stages assigned so far. This template describes the full chain for team projects and the final portfolio; it does not require unintroduced artifacts in Week 1. Project languages are chosen and justified, with one separate guided exercise in an unfamiliar language.

## The chain

| Stage | File | Written by | Approved by |
|---|---|---|---|
| Plan | `intent/<name>.md` | The agent, after interviewing you | You |
| Design | `spec.md` | The agent, from the approved intent | You, against the intent |
| Build | `plan.md`, then code on a branch | The agent | You, before any code |
| Test | tests, lint, CI | The agent | You confirm the loop actually ran |
| Deploy | a pull request reviewed against `REVIEW.md` | A separate reviewing agent | You merge |
| Maintain | a new `intent/<name>.md` | Triggered by a bug, a ticket, or a model change | You triage |

`CLAUDE.md` and `REVIEW.md` travel with the repo and are graded artifacts.

## Rules that are graded

- Intent and spec exist before code. Plan is approved before implementation. The commit history shows it.
- One pull request per feature, from a branch, reviewed before merge. Do not commit to `main` directly after the first commit.
- `spec.md` states the **language** and the **model** for each component and why.
- `ANNOTATION.md` answers the four questions for the finished project.
- No secrets in the repo. `.claude/settings.local.json` and `.env` are ignored; the `.example` file shows the shape.

## Submitting

Tag the commit you are submitting and put the repository URL plus the tag on Moodle:

```
git tag tp1-submitted
git push origin tp1-submitted
```

Tags the course uses: `intent-spec`, `tp1-submitted`, `tp2-submitted`, `portfolio-final`.

## Running the agent

Copy `.claude/settings.local.json.example` to `.claude/settings.local.json` and fill in your OpenRouter key and model slugs, or use the `orclaude` launcher from the [course repository](https://github.com/kousen/ai-integration-course/tree/main/scripts).
