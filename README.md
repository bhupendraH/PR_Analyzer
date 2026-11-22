# PR_Analyzer

Unattended Claude Code routine that analyses a Purchase Register (one entity,
one month range) from Azure Blob and writes one Excel workbook: spend by month,
supplier and category, supplier concentration, pattern-level anomalies and
transaction-level exceptions.

- `instructions.txt`: the analysis rules
- `RUNNER.md`: how a cloud run works
- `SETUP.md`: Blob layout, cloud environment, saved prompt, fire call
- `blob.py`: Blob I/O, limited to one run folder
- `template/`: output template generator and example config
