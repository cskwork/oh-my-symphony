# Scenario generation - from a simple prompt to concrete cases

Input: a URL plus at most one sentence of intent ("QA this", "test login",
"check the new upload feature"). Output: scenario YAMLs a non-developer can read.

## Case design stencil (apply per feature you found while exploring)

1. **Happy path** - the flow a normal user completes; assert the success indicator
   (welcome text, list row appears, URL changes).
2. **Validation** - required fields empty, wrong formats; assert the error message shows.
3. **Error/negative** - wrong credentials, cancelled dialogs, dismissed popups; assert
   the app stays usable (no dead end).
4. **Edge** - empty states, long/Unicode input, double-click, back button after submit.
5. **Transitions** - every click you found that opens a new tab/popup gets its own step
   with `expect_popup: true` plus an assertion inside the new tab.

Cover breadth first (one happy path per menu), then depth on the feature the prompt
names. 5-10 scenarios is a good first pass for a whole site; 2-4 for one feature.

## Rules

- One scenario = one user goal. Name it in the user's language:
  `로그인-정상`, `자료등록-필수값누락`.
- Every step has a `description` a non-developer understands
  ("'로그인' 버튼 클릭", not "click #btn-32").
- Selector preference: `testid` > stable `css` id > `role`+`name` > `text`.
  Use the dict form so the engine can fall back (`reference/scenario-format.md`).
- End every scenario with at least one `expect_visible` / `expect_text` / `expect_url` -
  a scenario without assertions proves nothing.
- Credentials and any per-user values are `{{vars}}`, never literals.
- Known dialogs/popups belong in steps (`expect_popup`) so an UNEXPECTED one still
  surfaces as a side effect.

## Post-feature testing (developer handoff)

When the prompt is "I finished feature X, test it": generate cases ONLY for X using the
stencil, add them to the site's existing scenario dir, then run the whole site sweep so
side effects in neighboring screens are caught too.
