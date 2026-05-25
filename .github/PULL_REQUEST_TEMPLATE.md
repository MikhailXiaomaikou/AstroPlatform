## Summary

<!-- What changed and why, in 1-3 sentences. -->

## Failure-category mapping

<!-- Required for fixes that address a blind-test finding. Map this change to
one or more categories from docs/BLIND_RESEARCH_TESTING_LOG.md "Failure
Categories" so fixes stay tied to observed failures instead of drifting into
feature-piling. -->

- Failure category: <!-- e.g. unsupported_numeric_claim, wrong_dataset_routing,
  config_only_overclaimed, ui_process_failure, honest_scope_gap, ... -->
- Observed in: <!-- blind-test round / paper class / issue ref — do NOT paste hidden paper answers here -->

## Regression test

- [ ] Added/updated a test that fails before this change and passes after

## Anti-hardcoding check (research-tool fixes)

- [ ] No paper conclusion / posterior value / fitted slope / tension is hardcoded
- [ ] No specific test prompt is special-cased
- [ ] Fix is a general capability (dataset registry / runner / routing / validation), not an answer
