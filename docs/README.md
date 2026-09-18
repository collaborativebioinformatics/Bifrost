# Documentation

## Current reference material

- [Results](results.md) holds the measured output: allele frequency, linear regression, suppression and straggler scenarios, and the scaling figures. The root [README](../README.md#results) carries only the summary.
- [Variable dictionary](variables.md) describes the implemented canonical variables and the mock-TRE local names. Those names are synthetic and are not schemas agreed with real TREs.
- [Demo specs](demo_specs.md) describes the example analysis specifications in `spec/examples/` and their documented limits.
- [Controlled local-steps comparison](local_steps_controlled.md) records the federated-averaging measurements that vary only `local_steps`, with the environment they were taken in and what they do and do not establish.

## Planning and architecture

- [Roadmap](roadmap.md) holds the original project goals in full. They are targets and historical intent, not current behaviour.
- [Build plan](BUILD_PLAN.md) is the milestone-based implementation plan. It records proposed and historical intent; use the repository and root [README](../README.md) to establish current behavior.
- [API architecture use case](architecture/API_architecture_use_case.txt) is a proposal.
- The current implemented flow is documented in the editable [Draw.io source](architecture/flowchart.drawio), [SVG overview](architecture/flowchart_drawio.svg), and [PNG overview](architecture/flowchart.png). Regenerate the SVG and PNG from the Draw.io source when changing the overview.

## Scaling evidence

- [Scaling plot](scaling.png) and [scaling data](scaling.json) record the existing simulation evidence.

## Local artifacts

`notes/` is ignored. Keep drafts and reports in `notes/reviews/`, decks in `notes/presentations/`, and cleanup records in `notes/maintenance/`. Add a file deliberately only when it is project documentation intended for collaborators.
