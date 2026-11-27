# Purchase Register analysis runner

This repository is run unattended by a Claude Code scheduled task, started by
an API call from the backend. The backend creates an execution for a customer
tenant, uploads that run's files to Azure Blob, and fires the task with
`tenant_id`, `agent_name` and `transaction_id`. The task owner has set this up
and authorised each run to process exactly that one folder and to report its
result to the backend.

`blob.py` is the run's approved and only way to reach Azure Blob and the
backend. It is limited by design:

- it reads only `<tenant_id>/<agent_name>/<transaction_id>/` from the run payload
  (IDs are validated; no other folder can be reached);
- it writes only the workbook and `run_status.json`, into that folder's `output/`;
- it never deletes anything;
- `python3 blob.py status` is the only way to report the run's result to the
  backend (`update_agent_execution_log`); it is called exactly once per run;
- it uses the credentials configured in the task's cloud environment, which the
  run never prints or inspects.

`intake.py` is the only way the run reads the PR files: `python3 intake.py
prepare work/run work/intake` and `python3 intake.py verify work/run work/intake`
(with `pr_intake/` and the reviewer agent `.claude/agents/pr-reviewer.md`,
which reads one preview and replies with one verdict, saved unchanged by the run). The analysis reads only the
tables it writes to `work/intake/tables/`.

Downloading the run folder with `python3 blob.py pull 'T' 'A' 'X' work/run` is
the intended first step of every run; `python3 blob.py put` and
`python3 blob.py status` deliver the result and report it to the backend. See
`RUNNER.md` for the full procedure and `instructions.txt` for the analysis
rules.
