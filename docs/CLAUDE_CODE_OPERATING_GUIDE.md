# Claude Code Operating Guide

## 1. Purpose

Use Claude Code with Claude Opus 5 as a planning, implementation, review, and documentation partner.

Official references:

- Claude Code overview: https://docs.anthropic.com/en/docs/claude-code/overview
- Project memory and CLAUDE.md: https://docs.anthropic.com/en/docs/claude-code/memory
- Skills: https://docs.anthropic.com/en/docs/claude-code/skills
- Subagents: https://docs.anthropic.com/en/docs/claude-code/sub-agents
- Hooks: https://docs.anthropic.com/en/docs/claude-code/hooks-guide
- Common workflows: https://docs.anthropic.com/en/docs/claude-code/common-workflows
- Model overview: https://docs.anthropic.com/en/docs/about-claude/models/overview
- Claude Opus 5: https://docs.anthropic.com/en/docs/about-claude/models/whats-new-claude-4-8

## 2. Operating principles

- Plan one vertical slice at a time.
- Verify external APIs before relying on them.
- Read product documents before changing scope.
- Prefer real integration failures over fictional success.
- Preserve decision history.
- Review completed work from a skeptical perspective.
- Keep product state and open questions current.
- Do not let implementation convenience redefine the product.

## 3. Starting a new implementation session

Provide this instruction:

> Read `CLAUDE.md`, the PRD, the architecture, the VideoDB integration specification, the verification engine specification, the quality plan, the delivery roadmap, and the decisions log. Identify the current phase and produce a concise implementation plan for only the next coherent vertical slice. Verify current official external documentation before relying on provider behavior. Do not implement anything until the plan includes acceptance criteria, failure states, and integrity checks.

## 4. End-of-session review

Provide this instruction:

> Review the completed work as a skeptical staff engineer and product integrity reviewer. Look for fabricated integration behavior, hidden fixture fallbacks, incorrect provider assumptions, unsupported certainty, missing provenance, absence treated as proof, weak authorization, non-idempotent asynchronous work, missing error states, and documentation drift. Fix critical issues and update the decision log and current project state.

## 5. Recommended subagents

### VideoDB integration reviewer

Use for:

- verifying current documentation;
- checking provider assumptions;
- identifying asynchronous behavior;
- checking search and playback behavior;
- validating failure states.

### Verification quality reviewer

Use for:

- checking rule semantics;
- deterministic evaluation;
- absence policy;
- uncertainty;
- evidence provenance;
- calibration.

### Product workflow reviewer

Use for:

- checking user journeys;
- avoiding implementation-led product drift;
- checking reviewer efficiency;
- finding missing edge cases.

### Security reviewer

Use for:

- multi-tenant isolation;
- stream access;
- webhook security;
- retention;
- audit integrity;
- prompt injection.

## 6. Context management

Keep root `CLAUDE.md` concise enough to remain useful. Put detailed domain information in the documents linked from it.

At the beginning of each major phase, update:

- decisions;
- open questions;
- current service assumptions;
- quality findings;
- next acceptance criteria.

## 7. Anti-patterns

Do not ask Claude Code to:

- build the whole product in one request;
- infer missing external API details;
- skip error states;
- create polished fixture results before real integration;
- optimize architecture before a vertical slice works;
- convert every subjective requirement into an automated score;
- hide unfinished work behind generic placeholders;
- overwrite historical reports during re-evaluation.
