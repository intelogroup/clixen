# Hands-Free Automation Roadmap

**Goal:** Turn the current starter-pack automations into durable hands-free workflows with scheduling, approvals, and stateful execution.

## Phase 1: Shared Platform

### Scope
- durable workflow instances
- approval requests
- persistent notifications
- minimal workflow and approval APIs

### Files
- Create: `tools-harness/store/workflow_store.py`
- Modify: `tools-harness/tools/notifications.py`
- Modify: `tools-harness/chat_ui.py`
- Modify: `tools-harness/test_chat_ui_endpoints.py`

### Data Model

`workflow_instances`
- `id`
- `automation_id`
- `task_name`
- `status`
- `schedule_json`
- `config_json`
- `last_run_at`
- `next_run_at`
- `last_result_json`
- `dedupe_key`
- `created_at`
- `updated_at`

`approval_requests`
- `id`
- `workflow_instance_id`
- `job_id`
- `kind`
- `payload_json`
- `status`
- `expires_at`
- `resolved_at`
- `created_at`
- `updated_at`

`notifications`
- `id`
- `ts`
- `level`
- `source`
- `message`
- `action_label`
- `action_url`
- `action_type`
- `action_payload_json`
- `read`

### API Surface
- `GET /workflows`
- `POST /workflows`
- `GET /approvals`
- `POST /approvals`
- `POST /approvals/{id}/approve`
- `POST /approvals/{id}/reject`

## Phase 2: Persistent Watchlists

### Why First
- lowest execution risk
- validates scheduler, dedupe, and notification history
- strongest signal that the system is now truly recurring

### New Behavior
- save watch topics as workflow instances
- schedule recurring runs
- store fingerprints of prior result sets
- alert only on material deltas

### New Components
- `jobs/watchlist_job.py`
- search result normalization helper
- workflow scheduler loop

## Phase 3: Auto-Draft Email Copilot

### New Behavior
- recurring inbox scan
- classify messages into summarize, task, ignore, draft
- produce reply drafts
- gate send via approval

### New Components
- `jobs/email_copilot_job.py`
- Gmail thread helper and draft helper
- draft preview in notification payloads

## Phase 4: Approval-Gated Browser Chore Runner

### New Behavior
- run browser task until irreversible checkpoint
- create approval request with snapshot
- resume after approval

### New Components
- `jobs/browser_chore_job.py`
- checkpoint/resume state on workflow instance or job metadata
- screenshot and extracted summary in approval payload

## Delivery Order
1. shared platform
2. persistent watchlists
3. auto-draft email copilot
4. browser chore runner

## Success Criteria
- workflows survive server restart
- notifications survive server restart
- approval requests can be created and resolved through API
- recurring watchlists suppress duplicate alerts
- email drafts are never sent without explicit approval
- browser flows stop before irreversible actions and can resume cleanly
