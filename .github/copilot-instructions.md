# Copilot Instructions (General)

These instructions are project-agnostic and should apply to most repositories.

## Safety and Workflow

- Never call task_complete automatically. Always wait for the user to explicitly confirm the task is done before marking it complete.
- Never merge changes to `main` directly. Always commit to the current working branch.
- In agent mode, do not create commits. Only edit files requested by the user.
- Prefer small, reversible changes and validate with the narrowest relevant checks first.
- Preserve existing conventions unless the user asks for a broader refactor.

## Context Gathering

- Before implementation, inspect the local project context (for example: `README`, package/tooling files, project-level instructions).
- If any assumption is required, stop and ask the user clarifying questions before implementing.
- Do not assume missing requirements, inputs, constraints, or expected behavior.

## Context Reuse and Token Efficiency

- Reuse previously gathered context in the same session whenever still valid.
- Do not re-scan the full project if required files were already analyzed.
- Re-scan only when:
	- user explicitly asks for a fresh scan
	- new commits or edits invalidate earlier findings
	- the task scope changes to new areas
- Prefer targeted, diff-based, or file-scoped reads over repository-wide scans.

## Agent Discovery and Recommendation

- Check whether task-specific agents exist (for example under `.github/agents/`).
- If a matching agent exists, suggest using it and explain why it fits the task.
- If no suitable agent exists, proceed normally with direct implementation.

## Optional Two-Agent Test Isolation Workflow

- When the user asks for independent verification, prefer a two-agent flow:
	- `test-oracle` writes tests and acceptance criteria from requirements.
	- `blind-implementer` implements from requirements without reading tests.
- Keep test files hidden from the implementation step when using this flow.
- Use this workflow only when requested or clearly beneficial; do not force it for all tasks.

## Design-System-Aware Behavior

- For UI, styling, or layout tasks, look for a design specification before making visual changes.
- Prefer `DESIGN.md` when present (commonly at project root, `.github/DESIGN.md`, or feature design folders).
- If no design spec exists, preserve current visual language and avoid introducing a conflicting style.

## Commit and PR Conventions

- **Format**: All commits and PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/).
- **Types**: Use specific types defined in `.releaserc.json` to trigger the correct release:
  - `feat`: Minor release (new features).
  - `fix`, `perf`, `refactor`, `style`: Patch release (bug fixes, performance, code changes).
  - `chore`, `docs`, `test`, `ci`, `build`: No release (maintenance and documentation).
- **Breaking Changes**: Use `!` after the type (e.g., `feat!:`) and include `BREAKING CHANGE:` in the footer for major releases.
- **PR Titles**: Ensure the PR title is also a valid conventional commit message, as it is often used for the final squashed commit.

## Response Style

For coding-task final responses only:

- If files changed, list each changed file with a concise reason.
- If exactly one file changed, do not add a separate summary.
- If more than one file changed, add a final summary under 50 words.
- If no files changed, say so in one short sentence.
- Mention tests or validation that were run, including failures or unavailable checks.

For questions, reviews, brainstorming, and status updates:

- Answer the user's request directly.
- Do not force the “files changed” format.
- Do not show reasoning or implementation details unless the user asks for them.
- For reviews, list findings first, ordered by severity.