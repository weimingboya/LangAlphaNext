# LangAlpha Project Switcher Design QA

- Source visual truth: `/Users/xieyingshuai/.codex/generated_images/019fa291-6456-7163-9312-2b05d2b19861/call_dBME9cVFTKZnYFThWgZ0nmJ6.png`
- Desktop implementation: `/tmp/langalpha-sidebar-open-desktop-final.png`
- Mobile implementation: `/tmp/langalpha-sidebar-open-mobile.png`
- State: project menu open
- Source pixels: 858 × 1832
- Desktop viewport and screenshot: 1440 × 1024 CSS px, browser DPR 2, screenshot normalized to 1440 × 1024 px
- Mobile viewport and screenshot: 390 × 844 CSS px, screenshot normalized to 390 × 844 px

## Full-view comparison evidence

The implementation preserves the selected target's hierarchy: compact brand, one-line
project selector, grouped project menu, one primary New research action, pale-mint
active research row, and quiet sign-out action. The existing product's 248 px desktop
rail is intentionally retained instead of copying the concept image's illustrative
frame width.

## Focused region comparison evidence

The 390 × 844 capture verifies the complete sidebar at the responsive breakpoint.
The 292 px drawer, 263 px project menu, primary action, recent rows, destructive
action color, and bottom sign-out remain visible without horizontal overflow
(`body.scrollWidth = 390`).

## Fidelity surfaces

- Fonts and typography: existing Inter/Geist stack retained; weights and sizes match
  the concept's compact product typography and truncate long project/thread names.
- Spacing and layout rhythm: 40–44 px controls, 7–10 px radii, compact section gaps,
  and in-flow menu spacing match the chosen direction.
- Colors and tokens: existing forest green, mint selection, neutral borders, muted
  text, and semantic danger red are reused consistently.
- Image and icon quality: the component has no raster imagery; all new interface
  icons come from the Phosphor icon library and use one visual family.
- Copy and content: project actions, confirmation language, primary action, Recent,
  and Sign out are present and clearly grouped.

## Interaction and accessibility evidence

- Project menu opens by click or ArrowDown.
- ArrowUp/ArrowDown/Home/End move focus through enabled menu items.
- Escape backs out of an editor first, then closes the menu and restores trigger focus.
- Clicking outside closes the menu.
- Create and rename use an inline labeled input.
- Delete uses an inline alert dialog with explicit consequences and confirmation.
- Desktop and mobile console checks returned no warnings or errors.

## Comparison history

1. Initial capture: `/tmp/langalpha-sidebar-open-desktop.png`
   - P1: the absolutely positioned menu covered New research and the top of Recent.
   - Fix: changed the menu to participate in the sidebar flow while retaining its
     elevated popover treatment.
2. Post-fix capture: `/tmp/langalpha-sidebar-open-desktop-final.png`
   - New research and Recent remain visible below the open menu.
   - No actionable P0, P1, or P2 mismatch remains.

## Follow-up polish

- P3: relative timestamps shown in the generated concept remain intentionally omitted
  because the current thread presentation does not expose that UI metadata.

final result: passed
