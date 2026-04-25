# FR × Task Coverage Matrix

This precheck matrix is the handoff artifact for `/speckit-analyze`.
It maps spec requirements to the implemented tasks so the next analysis
pass can verify coverage and spot any gaps before merge.

| FR / SC | Coverage |
| --- | --- |
| FR-001 | T007, T016, T091 |
| FR-002 / FR-022 / FR-023 | T048~T054 |
| FR-003 | T044, T097, T098 |
| FR-004 / FR-005 | T008, T017, T028, T029, T039 |
| FR-006 / FR-019 / FR-020 / FR-021 | T014, T023, T027, T030, T031 |
| FR-007 / FR-008 / FR-009 | T010, T019, T040, T076 |
| FR-010 / FR-011 / FR-012 | T055~T061 |
| FR-013 / FR-014 / FR-015 | T062~T069 |
| FR-016 / FR-017 / FR-018 | T041, T073, T074, T077 |
| FR-024 | T007~T024, T103 |
| FR-025 / FR-026 / FR-027 | T034, T035, T088, T092, T093 |
| FR-028 / FR-029 / FR-030 | T079~T087 |
| FR-031 | T032, T033, T089, T091 |
| SC-001 | T101 |
| SC-002 | T036, T047 |
| SC-003 | T048 |
| SC-004 | T062, T069 |
| SC-005 | T070, T078 |
| SC-006 | T030, T031, T082 |
| SC-007 | T055 |
| SC-008 | T030, T031 |
| SC-009 | T079, T087 |
| SC-010 | T080, T087 |
| SC-011 | T089, T094 |

## Notes

- T098 (feishu_stub) and T099 (LLMClient) are Polish-phase abstraction
  additions and do not widen any existing contract surface.
- This file exists so the next `/speckit-analyze` run has a single, explicit
  task-to-requirement index to consume.
