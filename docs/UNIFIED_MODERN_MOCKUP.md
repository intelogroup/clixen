# Clixen MVP - Unified Modern UI Mockup

## Product Overview
**Clixen** - Natural language workflow automation platform that transforms conversational prompts into executable n8n workflows.

## Design System

### Visual Language
```
BRAND COLORS          STATUS INDICATORS       UI ELEMENTS
Primary:   #3B82F6    Active:    ● Green      Buttons:  [Action]
Secondary: #6366F1    Paused:    ◐ Blue       Links:    →Link
Accent:    #8B5CF6    Draft:     ○ Gray       Icons:    ⚡ 📊 💬
Dark:      #1F2937    Failed:    ✕ Red        Dividers: ─────────
Light:     #F9FAFB    Warning:   ⚠ Yellow     Cards:    ┌─────┐
                                               │     │
                                               └─────┘
```

---

## 1. Authentication Flow

### Sign In / Sign Up
```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│                           ⚡ Clixen                              │
│                   Natural Language Automation                    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │                    Welcome Back                          │  │
│  │                                                          │  │
│  │  Email                                                   │  │
│  │  ┌────────────────────────────────────────────────┐     │  │
│  │  │ your@email.com                                  │     │  │
│  │  └────────────────────────────────────────────────┘     │  │
│  │                                                          │  │
│  │  Password                                         👁      │  │
│  │  ┌────────────────────────────────────────────────┐     │  │
│  │  │ ••••••••                                        │     │  │
│  │  └────────────────────────────────────────────────┘     │  │
│  │                                                          │  │
│  │  ┌────────────────────────────────────────────────┐     │  │
│  │  │              Sign In                            │     │  │
│  │  └────────────────────────────────────────────────┘     │  │
│  │                                                          │  │
│  │  ─────────────────── OR ───────────────────           │  │
│  │                                                          │  │
│  │  New to Clixen? →Create Account                         │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│                 Transform ideas into workflows                   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Enhanced Dashboard with Sidebar

### Active Workflows View with Navigation
```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ ┌─ SIDEBAR (300px) ──┐ ┌────────── MAIN CONTENT (900px) ───────────────┐                │
│ │                    │ │                                                │                │
│ │ ⚡ Clixen           │ │ ┌─ HEADER ─────────────────────────────────────┐ │                │
│ │ ──────────────────  │ │ │ Workflows        [📊 Analytics] [👤 Profile] │ │                │
│ │                    │ │ └──────────────────────────────────────────────┘ │                │
│ │ [+ New Chat]       │ │                                                │                │
│ │                    │ │ ┌─ WORKFLOW CONTROLS ─────────────────────────┐ │                │
│ │ Navigation         │ │ │ Active (3)  Draft (1)  All (4) [+ Create New] │ │                │
│ │ ──────────────────  │ │ │                          🔍 Search workflows │ │                │
│ │ 🏠 Dashboard       │ │ └──────────────────────────────────────────────┘ │                │
│ │ 💬 Chat            │ │                                                │                │
│ │ 📊 Analytics       │ │ ┌─ WORKFLOW LIST ─────────────────────────────┐ │                │
│ │ ⚙️ Settings        │ │ │                                              │ │                │
│ │                    │ │ │ ┌─ Workflow Card ───────────────────────────┐ │ │                │
│ │ Workspaces         │ │ │ │ ● Daily Email Report            ⋮ Actions │ │ │                │
│ │ ──────────────────  │ │ │ │   Analytics summary every morning        │ │ │                │
│ │ [▼] Personal (4)   │ │ │ │   Daily 9:00 AM • Next: in 14h           │ │ │                │
│ │  ├─● Email Reports │ │ │ │   ▓▓▓▓▓▓▓▓▓▓ 127 successful runs        │ │ │                │
│ │  ├─● Weather Alert │ │ │ └───────────────────────────────────────────┘ │ │                │
│ │  ├─◐ Data Backup   │ │ │                                              │ │                │
│ │  └─○ News Digest   │ │ │ ┌─ Workflow Card ───────────────────────────┐ │ │                │
│ │                    │ │ │ │ ● Customer Onboarding           ⋮ Actions │ │ │                │
│ │ [▼] Business (8)   │ │ │ │   Multi-step automation for new signups  │ │ │                │
│ │  ├─● Lead Capture  │ │ │ │   Webhook • Last triggered: 2h ago       │ │ │                │
│ │  ├─● CRM Sync      │ │ │ │   ▓▓▓▓▓▓▓░░░ 89 runs • 5 failures       │ │ │                │
│ │  ├─● Invoicing     │ │ │ └───────────────────────────────────────────┘ │ │                │
│ │  ├─◐ Team Reports  │ │ │                                              │ │                │
│ │  ├─○ Social Posts  │ │ │ ┌─ Workflow Card ───────────────────────────┐ │ │                │
│ │  ├─○ Survey Auto   │ │ │ │ ◐ Weekly Backup                 ⋮ Actions │ │ │                │
│ │  ├─○ Support Tix   │ │ │ │   Database backup to cloud storage       │ │ │                │
│ │  └─○ Inventory     │ │ │ │   Sundays 2:00 AM • Status: Paused       │ │ │                │
│ │                    │ │ │ │   ▓▓▓▓▓▓▓▓▓▓ 52 successful runs          │ │ │                │
│ │ [▶] Archive (12)   │ │ │ └───────────────────────────────────────────┘ │ │                │
│ │                    │ │ │                                              │ │                │
│ │ ──────────────────  │ │ │ ┌─ Workflow Card ───────────────────────────┐ │ │                │
│ │ 👤 Profile         │ │ │ │ ○ Social Media Monitor  [Deploy Workflow] │ │ │                │
│ │ 🚪 Sign Out        │ │ │ │   Track mentions and respond automatically│ │ │                │
│ │                    │ │ │ │   Status: Draft • Created: Yesterday     │ │ │                │
│ │                    │ │ │ │   Ready for deployment                   │ │ │                │
│ │                    │ │ │ └───────────────────────────────────────────┘ │ │                │
│ │                    │ │ │                                              │ │                │
│ │                    │ │ └──────────────────────────────────────────────┘ │                │
│ │                    │ │                                                │                │
│ └────────────────────┘ └────────────────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### Enhanced Empty State with Sidebar
```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ ┌─ SIDEBAR (300px) ──┐ ┌────────── MAIN CONTENT (900px) ───────────────┐                │
│ │                    │ │                                                │                │
│ │ ⚡ Clixen           │ │ ┌─ HEADER ─────────────────────────────────────┐ │                │
│ │ ──────────────────  │ │ │ Workflows        [📊 Analytics] [👤 Profile] │ │                │
│ │                    │ │ └──────────────────────────────────────────────┘ │                │
│ │ [+ New Chat]       │ │                                                │                │
│ │                    │ │ ┌─ EMPTY STATE ───────────────────────────────┐ │                │
│ │ Navigation         │ │ │                                              │ │                │
│ │ ──────────────────  │ │ │                   🚀                        │ │                │
│ │ 🏠 Dashboard       │ │ │                                              │ │                │
│ │ 💬 Chat            │ │ │          Create Your First Workflow         │ │                │
│ │ 📊 Analytics       │ │ │                                              │ │                │
│ │ ⚙️ Settings        │ │ │   Describe what you want to automate in     │ │                │
│ │                    │ │ │   plain English and let AI build it for you │ │                │
│ │ Quick Start        │ │ │                                              │ │                │
│ │ ──────────────────  │ │ │     ┌──────────────────────────────┐       │ │                │
│ │ 🚀 Templates       │ │ │     │     Start with AI Chat       │       │ │                │
│ │ • Email Reports    │ │ │     └──────────────────────────────┘       │ │                │
│ │ • Data Backup      │ │ │                                              │ │                │
│ │ • Social Media     │ │ │   Or choose from popular automations:       │ │                │
│ │ • Customer Flow    │ │ │   • Daily reports and notifications          │ │                │
│ │                    │ │ │   • Data backup and synchronization         │ │                │
│ │ Learning           │ │ │   • Customer communication flows            │ │                │
│ │ ──────────────────  │ │ │   • Social media management                 │ │                │
│ │ 📖 Documentation   │ │ │                                              │ │                │
│ │ 💡 Examples        │ │ │   ┌─ Quick Templates ─────────────────────┐  │ │                │
│ │ 🎥 Video Guides    │ │ │   │ [📧 Email] [💾 Backup] [📊 Reports] │  │ │                │
│ │ 🆘 Support         │ │ │   │ [📱 Social] [💼 CRM] [🔔 Alerts]   │  │ │                │
│ │                    │ │ │   └─────────────────────────────────────┘  │ │                │
│ │ ──────────────────  │ │ │                                              │ │                │
│ │ 👤 Profile         │ │ └──────────────────────────────────────────────┘ │                │
│ │ 🚪 Sign Out        │ │                                                │                │
│ └────────────────────┘ └────────────────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### Empty State
```
┌──────────────────────────────────────────────────────────────────┐
│ ⚡ Clixen              [📊 Analytics] [💬 New Chat] [👤 Profile] │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │                     🚀                                   │  │
│  │                                                          │  │
│  │            Create Your First Workflow                    │  │
│  │                                                          │  │
│  │     Describe what you want to automate in plain         │  │
│  │     English and let AI build it for you                 │  │
│  │                                                          │  │
│  │          ┌──────────────────────────────┐               │  │
│  │          │     Start with AI Chat       │               │  │
│  │          └──────────────────────────────┘               │  │
│  │                                                          │  │
│  │     Popular automations:                                │  │
│  │     • Daily reports and notifications                    │  │
│  │     • Data backup and synchronization                   │  │
│  │     • Customer communication flows                      │  │
│  │     • Social media management                           │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Chat Interface

### AI Conversation View
```
┌──────────────────────────────────────────────────────────────────┐
│ ← Dashboard           Workflow Chat             [Clear] [Export] │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ AI  Hello! I can help you create automated workflows.   │   │
│  │     What would you like to automate today?              │   │
│  │                                                 9:42 AM │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│                    ┌─────────────────────────────────────┐      │
│                    │ I need to send weekly reports to my │      │
│                    │ team every Monday morning           │      │
│                    │                          You 9:43 AM│      │
│                    └─────────────────────────────────────┘      │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ AI  Great! I'll help you set up a weekly report         │   │
│  │     workflow. Let me ask a few questions:               │   │
│  │                                                         │   │
│  │     1. What time should the reports be sent?            │   │
│  │     2. Which email addresses should receive them?       │   │
│  │     3. What data should be included in the reports?     │   │
│  │                                                 9:43 AM │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│                    ┌─────────────────────────────────────┐      │
│                    │ 9 AM EST, send to team@company.com  │      │
│                    │ Include sales and user metrics      │      │
│                    │                          You 9:44 AM│      │
│                    └─────────────────────────────────────┘      │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ AI  Perfect! I'm creating your workflow now...          │   │
│  │                                                         │   │
│  │     📋 Weekly Team Report                               │   │
│  │     ├─ Schedule: Mondays at 9:00 AM EST                 │   │
│  │     ├─ Recipients: team@company.com                     │   │
│  │     └─ Data: Sales metrics, User analytics              │   │
│  │                                                         │   │
│  │     ▓▓▓▓▓▓▓▓░░ Creating workflow...                    │   │
│  │                                                 9:44 AM │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  Type a message...                              [📎] [→ Send]   │
└──────────────────────────────────────────────────────────────────┘
```

### Workflow Creation Success
```
┌──────────────────────────────────────────────────────────────────┐
│ ← Dashboard           Workflow Chat             [Clear] [Export] │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ AI  ✅ Workflow created successfully!                    │   │
│  │                                                         │   │
│  │     📋 Weekly Team Report                               │   │
│  │     Status: Ready to deploy                             │   │
│  │     ID: wf_abc123                                       │   │
│  │                                                         │   │
│  │     Your workflow is saved and ready to activate.       │   │
│  │     Would you like to deploy it now?                    │   │
│  │                                                         │   │
│  │     [Deploy Now]  [View Details]  [Edit Settings]       │   │
│  │                                                 9:45 AM │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  Type a message...                              [📎] [→ Send]   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Workflow Details

### Workflow Management View
```
┌──────────────────────────────────────────────────────────────────┐
│ ← Workflows          Daily Email Report                [Edit]    │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Overview    Executions    Settings    Chat History             │
│  ─────────────────────────────────────────────────────────────  │
│                                                                  │
│  Status                                                         │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ ● Active                                   [Pause] [Stop] │  │
│  │ Running successfully since Aug 1, 2024                   │  │
│  │ Next execution: Tomorrow at 9:00 AM EST                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Performance                                                    │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Success Rate    Avg Duration    Total Runs               │  │
│  │ 98.4%           1.2s            127                      │  │
│  │                                                          │  │
│  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ 98.4%        │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Configuration                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Trigger:     Schedule - Daily at 9:00 AM EST            │  │
│  │ Actions:     Fetch Data → Format Email → Send           │  │
│  │ Recipients:  john@example.com                           │  │
│  │ Created:     Aug 1, 2024 by AI Assistant                │  │
│  │ Modified:    Aug 5, 2024                                │  │
│  │ n8n ID:      [USR-u123] Daily Email Report              │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Recent Executions                                              │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Today 9:00 AM     ✓ Success    1.1s    View logs →     │  │
│  │ Aug 8 9:00 AM     ✓ Success    0.9s    View logs →     │  │
│  │ Aug 7 9:00 AM     ✓ Success    1.3s    View logs →     │  │
│  │ Aug 6 9:00 AM     ✓ Success    1.2s    View logs →     │  │
│  │ Aug 5 9:00 AM     ✕ Failed     0.1s    View error →    │  │
│  │                                                          │  │
│  │                    [View All Executions]                 │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. Mobile Responsive Views

### Mobile Dashboard
```
┌─────────────────┐
│ ≡  Clixen    👤 │
├─────────────────┤
│                 │
│ Workflows    [+]│
│ ───────────────│
│                 │
│ ┌─────────────┐ │
│ │● Daily Email│ │
│ │ Report      │ │
│ │ 9:00 AM     │ │
│ │ 127 runs    │ │
│ └─────────────┘ │
│                 │
│ ┌─────────────┐ │
│ │● Customer   │ │
│ │ Onboarding  │ │
│ │ Webhook     │ │
│ │ 89 runs     │ │
│ └─────────────┘ │
│                 │
│ ┌─────────────┐ │
│ │○ Social     │ │
│ │ Monitor     │ │
│ │ Draft       │ │
│ │ [Deploy]    │ │
│ └─────────────┘ │
│                 │
└─────────────────┘
```

### Mobile Chat
```
┌─────────────────┐
│ ← Chat       ⋮  │
├─────────────────┤
│                 │
│ ┌─────────────┐ │
│ │AI: Hello!   │ │
│ │What would   │ │
│ │you like to  │ │
│ │automate?    │ │
│ └─────────────┘ │
│                 │
│     ┌─────────┐ │
│     │You: Send│ │
│     │weekly   │ │
│     │reports  │ │
│     └─────────┘ │
│                 │
│ ┌─────────────┐ │
│ │AI: Creating │ │
│ │workflow...  │ │
│ │▓▓▓▓░░ 60%   │ │
│ └─────────────┘ │
│                 │
├─────────────────┤
│ Type...     [→] │
└─────────────────┘
```

---

## 6. Deployment & Status States

### Deployment Progress
```
┌──────────────────────────────────────────────────────────────────┐
│                     Deploying Workflow                          │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│                          🚀                                     │
│                                                                  │
│  Deploying "Weekly Team Report" to n8n                          │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  ✓ Workflow validated                                    │  │
│  │  ✓ Credentials configured                                │  │
│  │  ▓ Connecting to n8n instance...                         │  │
│  │  ○ Uploading workflow definition                         │  │
│  │  ○ Activating workflow                                   │  │
│  │  ○ Verifying deployment                                  │  │
│  │                                                          │  │
│  │  ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░ 45%                        │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│                 This may take 30-60 seconds                     │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Error State
```
┌──────────────────────────────────────────────────────────────────┐
│                      Deployment Failed                          │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│                          ⚠                                      │
│                                                                  │
│  Failed to deploy "Slack Integration"                           │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │  Error: Invalid Slack webhook URL                        │  │
│  │                                                          │  │
│  │  The webhook URL format is incorrect. Slack webhooks     │  │
│  │  should start with: https://hooks.slack.com/             │  │
│  │                                                          │  │
│  │  Current value: https://slack.com/webhook/xyz            │  │
│  │                                                          │  │
│  │     [Fix Configuration]    [Get Help]    [Cancel]        │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 7. User Profile & Settings

### Profile Dropdown
```
┌──────────────────────────────────────────────────────────────────┐
│ ⚡ Clixen              [📊 Analytics] [💬 New Chat] [👤 John ▼] │
├──────────────────────────────────────────────────────────────────┤
│                                              ┌─────────────────┐ │
│                                              │ John Doe        │ │
│                                              │ john@example.com│ │
│                                              │ Free Plan       │ │
│                                              ├─────────────────┤ │
│                                              │ My Workflows    │ │
│                                              │ Analytics       │ │
│                                              │ Settings        │ │
│                                              │ API Keys        │ │
│                                              ├─────────────────┤ │
│                                              │ Documentation   │ │
│                                              │ Support         │ │
│                                              ├─────────────────┤ │
│                                              │ Sign Out        │ │
│                                              └─────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## Design Principles

### Consistency
- Unified color scheme and status indicators across all views
- Consistent spacing and typography
- Standard button and action patterns

### Clarity
- Clear visual hierarchy with proper contrast
- Descriptive labels and helpful empty states
- Progressive disclosure of complex information

### Efficiency
- Minimal clicks to complete tasks
- Smart defaults and suggestions
- Batch operations where appropriate

### Accessibility
- High contrast ratios for readability
- Clear focus states for keyboard navigation
- Descriptive labels for screen readers
- Touch-friendly targets on mobile (44x44px minimum)

### Performance
- Progressive loading indicators
- Skeleton screens for content loading
- Optimistic UI updates
- Efficient state management

---

## Key User Flows

### First-Time User Journey
1. **Sign Up** → Simple email/password registration
2. **Welcome** → Clear value proposition on empty dashboard
3. **Create First Workflow** → Guided chat experience
4. **Deploy** → One-click deployment with progress feedback
5. **Success** → Celebration and next steps

### Returning User Flow
1. **Dashboard** → Overview of all workflows at a glance
2. **Monitor** → Quick status checks and performance metrics
3. **Manage** → Easy pause/resume/edit actions
4. **Create New** → Familiar chat interface for new automations

### Error Recovery Flow
1. **Error Detection** → Clear error indicators
2. **Diagnosis** → Helpful error messages with context
3. **Resolution** → Guided fix options or AI assistance
4. **Verification** → Test and confirm resolution

---

This unified mockup provides a consistent, modern interface that aligns with the Clixen MVP specification while maintaining clarity and usability across all screens and device sizes.