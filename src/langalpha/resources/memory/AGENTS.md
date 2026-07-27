# LangAlpha product memory

- The product has one agent experience; there are no Flash/PTC user modes.
- Daytona is the only code and file execution sandbox.
- New workspaces start empty. Do not assume bootstrap business files exist.
- Prefer Deep Agents native planning, filesystem, execution, skills, memory,
  summarization, and subagent capabilities over local replacements.
- Store user-visible outputs under `/workspace/artifacts/`.
