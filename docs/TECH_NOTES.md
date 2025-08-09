 # Clixen Technical Notes

 ## 1. Secret Management Strategy
 - **Backend-only key storage**: Store N8N_API_KEY and OPENAI_API_KEY exclusively in server environment variables; frontend never sees raw credentials.
 - **Edge Functions / API layer**: Proxy all calls to n8n and OpenAI via Supabase Edge Functions or backend API to sign requests with stored secrets.
 - **Optional per-project secrets**: If future per-team/project credentials are required, encrypt values in a dedicated `secrets` table using Postgres `pgcrypto` and decrypt at runtime in Edge Functions.

 ## 2. JSON Schema Validation and Self-Healing Loop
 - **Pre-deploy validation**: Call `validate-workflow` via n8n MCP server to catch schema errors before deployment.
 - **Runtime execution checking**: After deployment and execution, poll `GET /executions` to detect runtime errors.
 - **GPT-assisted repair**: On validation or execution errors, send error context back to GPT to auto-correct JSON (limit to 2 retries).
 - **Retry strategy**: Cap auto-repair attempts (schema + execution) at 3 combined; if still failing, surface error to user and log for manual review.

 ## 3. Versioning and Rollback
 - **Workflow versions**: Persist each deployed workflow version in Supabase `workflows` table with `version`, `json_payload`, and timestamps.
 - **Rollback endpoint**: Expose API to revert to a previous version; UI dropdown for selecting and redeploying prior versions.

 ## 4. Dashboard Pagination and Project Switching
 - **Pagination / infinite scroll**: Implement cursor-based pagination or lazy loading on the dashboard workflow list to support large data sets.
 - **Project switcher**: Dashboard UI includes project selector to navigate between multiple projects; defaults to the last active project.

 ## 5. Telemetry Scope and Quotas
 - **Core events only**: Capture only essential events (signup, workflow create, deploy, error) for MVP; defer full coverage to post-MVP.
 - **Rate limits and quotas**: Enforce per-user daily limits on GPT token usage and deployment attempts to manage costs.

 ## 6. End-to-End Testing and CI
 - **Staging tests**: Add CI jobs running Playwright/HTTP tests against a live n8n + MCP staging environment using canonical workflow (e.g., RSS→Slack).
 - **Scheduled smoke tests**: Periodically execute a simple workflow in staging to detect regressions.

 ## 7. Migration Strategy
 - **Supabase migrations**: Manage schema changes using Supabase Migrations; include rollback scripts for each change.

 ## 8. Security and Input Sanitization
- **User input sanitization**: Validate and escape all user prompts and JSON before forwarding to backend services; enforce CSP and secure headers in frontend.
- **CORS policy**: Restrict API endpoints to allowed origins and enforce secure transport (HTTPS).

## 9. CI/CD & Environments
- **Branch workflow**: Use Git flow or trunk-based workflow for feature branches, with pull requests targeting `staging` and promotion to `main` for production.
- **Deployment pipeline**: Configure Netlify (or GitHub Actions) to deploy `main` to production and `staging` to a staging environment.
- **Environment variables**: Document all required env vars (`.env.example`) for DEV, STAGING, and PROD in `docs/ENVIRONMENT.md`.

## 10. Logging & Observability
- **Backend logging**: Tag all requests with a unique request ID; log OpenAI, MCP, and n8n API call latencies and errors.
- **Frontend monitoring**: Capture uncaught exceptions and performance metrics via Sentry (integrate Sentry JS SDK).
- **Alerts**: Define Sentry alert rules for error rates and latency thresholds; integrate with Slack or email.

## 11. Feature Flags
- **Runtime toggles**: Control experimental features (e.g. JSON self-healing, version rollback) via environment-driven flags in Edge Functions or Supabase config.
- **Rollout strategy**: Enable features for internal users before broader release.

## 12. User Isolation in n8n Community Edition (MVP)
- **Workflow Naming Convention**: All workflows prefixed with `[USR-{userId}]` for identification and cleanup
- **Webhook Security**: Unique webhook paths per user: `webhook/{userHash}/{timestamp}/{random}`
- **Data Flow**: Frontend → Supabase (RLS) → Edge Functions → n8n; Dashboard queries only from Supabase
- **Cleanup Strategy**: Soft delete in Supabase, hard delete in n8n via cleanup script
- **Quota Management**: 10 workflows per user (configurable), enforced at Supabase level

## 13. 2-Way Synchronization Architecture
- **Sync Service**: Edge Function polling n8n execution status every 30 seconds
- **State Management**: Supabase as source of truth for workflow metadata
- **Real-time Updates**: Supabase Realtime subscriptions for dashboard updates
- **Error Recovery**: Failed deployments queued for retry with exponential backoff
- **Consistency**: Periodic reconciliation job to ensure Supabase/n8n alignment

## 14. MVP Security Considerations
- **Accepted Risks**: Shared n8n instance with user prefixing (not true isolation)
- **Mitigations**: RLS in Supabase, unguessable webhook URLs, user quotas
- **User Agreement**: Clear disclaimer about shared infrastructure for 50-user trial
- **Future Path**: Migrate to n8n Enterprise or container-per-user at scale
- **GDPR Compliance**: User data deletion script with full cleanup capability

## 12. Data Retention & Cleanup
- **Retention policy**: Archive or delete workflow versions and execution records older than 30 days via a scheduled Edge Function or Supabase scheduled trigger.
- **Cleanup jobs**: Automate pruning of stale records to maintain database performance.

## 13. API Schema Contracts & Versioning
- **OpenAPI spec**: Define and maintain API schemas for all backend endpoints in `docs/api/openapi.yaml`.
- **Runtime validation**: Use Zod or AJV to validate request and response payloads against schemas at runtime.

## 14. Authorization & RLS Policies
- **Supabase RLS**: Enforce Row-Level Security for `projects`, `workflows`, and `executions` tables to ensure users only access their own data.
- **Frontend guards**: Implement route-level checks and token verification in Edge Functions.

## 15. Accessibility & Internationalization
- **WCAG 2.1 AA**: Design and test UI components for color contrast, keyboard navigation, and ARIA roles.
- **i18n readiness**: Use a translation framework (e.g. react-i18next) and externalize all user-facing strings for future localization.

## 16. Performance Budgets
- **Bundle size**: Limit initial JS bundle to ≤200 KB gzipped; implement code-splitting for heavy modules.
- **Load metrics**: Monitor Lighthouse scores (FCP, TTI) and enforce budgets in CI via Lighthouse CI.
