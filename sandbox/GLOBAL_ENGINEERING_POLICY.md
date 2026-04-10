## Engineering Policy

- Always use explicit, human-readable names for variables, functions, parameters, classes, helpers, and intermediate values.
- Never use single-letter names except for trivial loop indices such as `i`, `j`, or `k`.
- Never use unclear abbreviations, transliterations, or project-local shorthand unless they are widely recognized in the relevant ecosystem.
- Prefer names such as `conversation_id`, `observation_id`, `reflection_count`, `tool_call`, `request_payload`, `response_data`, and `message_content` over shortened names such as `conv_id`, `obs_id`, `refl_cnt`, `tc`, `req`, `resp`, or `msg` when readability would be reduced.
- Optimize naming for fast comprehension by another engineer, not for typing speed.

- Always follow the repository's own execution workflow for validation.
- If the project uses Docker, CI-aligned scripts, Make targets, task runners, or project-specific commands for tests, lint, type checking, or builds, use those paths instead of personal shortcuts.
- Do not claim that code is verified unless verification was performed through the project's accepted workflow.

- Keep refactors behavior-preserving unless a behavior change is explicitly requested or required to fix a confirmed bug.
- Do not hide behavioral changes inside style cleanup, naming cleanup, formatting cleanup, or conventions cleanup.
- If a real bug is discovered during cleanup, isolate the behavioral fix in a separate commit whenever practical.

- Create a separate commit after each significant logical block of work.
- Keep commits narrowly scoped and easy to review.
- Do not combine unrelated cleanup, runtime fixes, refactors, and test drift updates in one commit when they can be separated cleanly.

- Prefer repository conventions over model defaults or personal stylistic preferences.
- Do not introduce a new naming style, helper pattern, abstraction style, or structural convention if the repository already has an established approach.
- When repository conventions conflict with model habits, repository conventions take precedence.

- Treat readability as a correctness concern.
- Write code so that another engineer can understand it quickly without relying on hidden chat context.
- Avoid clever, compressed, or overly abbreviated code when it reduces clarity.
- If a name or abstraction needs extra explanation to be understood, prefer rewriting it more clearly.
