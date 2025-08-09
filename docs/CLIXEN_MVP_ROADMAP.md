# Clixen MVP Roadmap

## Sprint 1: Authentication, Project Management & Core Chat UI (2 weeks)
- Initialize project scaffolding: React frontend and backend service template.
- Implement Supabase Auth (email/password): sign up, sign in, session management.
- Build project dashboard: create/select projects and list saved workflows with name, status, and creation date.
- Develop interactive chat UI: text prompt input, persistent chat history per project/workflow, "New Chat" button, and loading spinners.
- Reference existing UI screenshot assets (screenshots/ui-improvements and ui-test-results) for chat and dashboard design inspiration.
- Unit tests for auth flows, project API, and chat UI components.
- Implement project switcher and pagination/lazy-loading for workflow lists.

## Sprint 2: Workflow Generation & Persistence (2 weeks)
- Integrate OpenAI endpoint: process prompts to generate intermediate specs.
 - Integrate n8n MCP server: fetch node metadata (nodes, operations, parameters) to enrich prompts and validate generated workflows.
- Implement n8n JSON generator: map specs to workflow definitions.
- Persist workflows in Supabase: store workflow metadata and JSON payload.
- Integration tests for prompt-to-workflow pipeline and persistence.
- Add schema validation & self-healing loop tests (MCP validation + execution-error feedback).

## Sprint 3: n8n Deployment & Monitoring (2 weeks)
- Develop n8n API client: deploy and update workflows via REST API.
- Add deployment button in UI: trigger deployment with loading spinners and status feedback.
- Implement monitoring: poll deployment status and update workflow record.
- Integration tests for deployment flows against mocked n8n API.
- Implement versioning & rollback API and UI history dropdown for workflows.
- Implement input sanitization and security headers (CSP) in frontend and backend.

## Sprint 4: Mobile Responsiveness, Analytics & Documentation (2 weeks)
- Refine UI for mobile-friendly layouts and responsive design.
- Integrate telemetry: capture core events (sign ins, workflow creation, deployments, execution errors) and enforce per-user rate limits/quotas.
- Write developer and user documentation: quickstart guide, API references.
- Create Supabase migration scripts for workflows versioning and rollback schema.
- Finalize end-to-end tests and prepare v0.1.0 release.

## Long-Term Vision (Post-MVP)
- Introduce multi-agent orchestration for complex scenarios.
- Implement advanced error recovery and rollback capabilities.
- Expand enterprise features: billing, quotas, multi-tenant support.
- Curate marketplace of pre-built workflow templates.

*** End of File
