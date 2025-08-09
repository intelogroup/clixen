# Clixen MVP Specification

## 1. Project Overview

**Clixen** is a natural-language workflow creator that transforms user prompts into executable n8n workflows. This MVP focuses on delivering a simple, reliable experience for developers to define and deploy automation pipelines using conversational instructions.

## 2. BMAD Framework

### 2.1 Business Goals
- **Accelerate workflow automation**: Enable users to go from idea to running n8n workflow within minutes.
- **Lower technical barrier**: Provide an intuitive, chat-based interface so non-experts can build automations.
- **Drive adoption of n8n**: Showcase n8n capabilities via natural-language UX and seamless deployment.
- **Measure success**: Track user engagement (number of workflows created) and successful deployments.

### 2.2 Market & User Personas
| Persona             | Description                                          | Key Pain Points                           |
|---------------------|------------------------------------------------------|-------------------------------------------|
| Automation Enthusiast | Citizen developers wanting simple automations       | Steep learning curve of n8n visual editor  |
| Backend Engineer    | Developers building integrations and pipelines       | Manual JSON editing of workflows           |
| Operations Manager  | Teams coordinating cross-system processes            | Lack of visibility and reproducibility     |

### 2.3 Architecture & Core Features
This MVP comprises three layers:

1. **Chat Interface (Frontend)**
   - Single-page React app with text prompt, persistent chat history per project/workflow, and a "New Chat" button to start fresh conversations.
   - Natural-language guidance: proactively ask clarifying questions to perform feasibility checks, refine requirements, and guide the user through workflow definition.
   - Minimalistic interactive feedback: loading spinners and concise status messages (no JSON or diagram display).
2. **Workflow Engine (Backend)**
   - GPT-based processing: parse natural-language into an intermediate specification.
   - n8n JSON generator: map the spec to an n8n-compatible workflow definition.
   - n8n MCP Node Metadata: fetch dynamic node and parameter definitions from the MCP server to enrich prompts, guide node selection, and drive validation.
   - n8n MCP integration: use the Model Context Protocol to validate workflow feasibility, test connectivity against the target n8n instance, and manage workflow lifecycle states.
3. **Deployment Service**
   - n8n REST API integration: deploy or update workflows on a target n8n instance.
4. **Persistence & Project Management**
   - Supabase database: store users, projects, and workflow records.
   - Project dashboard API: list and retrieve saved workflows per project.
5. **Authentication & Authorization**
   - Supabase Auth (email/password): sign up, sign in, session management.
6. **Telemetry & Analytics**
   - Capture key events: workflow creation, deployment, status updates, and user sign-ins.

### 2.4 Out-of-Scope
- Multi-agent orchestration or specialist AI agents.
- Live workflow diagram rendering and interactive JSON editing.
- Advanced undo/redo or collaborative editing features.
- External OAuth providers beyond basic email/password.
- Rate-limiting, complex caching, or multi-tenant billing systems.

## 3. Acceptance Criteria
- Users can sign up and sign in via email/password.
- Users can create and select a project in a dashboard view.
- Within a project, enter a prompt describing an automation (e.g., “daily Slack reminder from RSS feed”).
- System engages in a feasibility-check dialogue, asking clarifying questions to refine requirements and obtain user confirmation before generating a valid n8n workflow behind the scenes and saving it to the project.
- Users can click “Deploy” to publish the workflow to a connected n8n instance, with loading spinners and deployment status indicators.
- Users can view created workflows in the project dashboard, displaying workflow name, status, and creation date. Selecting a workflow shows its chat history and details.
- Users can view and reset chat history within a project/workflow using the "New Chat" button.
- Clear error messages are displayed for prompt processing or deployment failures.

## 4. Success Metrics
- **User Onboarding**: ≥70% of new users complete their first workflow within 10 minutes of signup.
- **Workflow Persistence**: ≥90% of generated workflows are saved and retrievable in the dashboard.
- **Deployment Rate**: ≥80% of generated workflows are successfully deployed.
- **Telemetry Coverage**: Capture core user events (signup, workflow create, deploy, execution errors) in analytics; full coverage deferred post‑MVP.

## 5. Technical Notes
Refer to `docs/TECH_NOTES.md` for detailed implementation guidelines on secret management, JSON validation and self‑healing, versioning, pagination, telemetry quotas, testing, migrations, and security practices.

*** End of File
