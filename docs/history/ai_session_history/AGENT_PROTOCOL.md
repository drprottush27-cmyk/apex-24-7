# APEX AGENT HANDOFF PROTOCOL

The agents form a sequential pipeline.

PLANNER
  ↓
BUILDER
  ↓
TESTER
  ↓
SECURITY REVIEWER
  ↓
FINAL REVIEWER
  ↓
CHECKPOINT
  ↓
NEXT QUEUED TASK

Only the BUILDER can modify production code.

The TESTER and REVIEWERS are independent.

If TESTER fails:
    BUILDER receives failure report.

If SECURITY REVIEW fails:
    BUILDER receives security report.

If FINAL REVIEW fails:
    BUILDER receives final review report.

No task may be marked COMPLETE without:
- implementation
- passing tests
- security review
- final review
- Git checkpoint
- SESSION_STATE update

Every handoff must be written to:

.ai/handoffs/

Every completed task must update:

.ai/SESSION_STATE.md
.ai/TEST_STATUS.md
.ai/COMPLETED.md
.ai/TODO.md

The system must be recoverable after:
- SSH disconnect
- terminal closure
- VPS reboot
- model quota exhaustion
- agent crash
- test failure
