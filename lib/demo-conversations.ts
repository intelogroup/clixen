// Demo conversations showcasing sophisticated chat interaction patterns
// Based on the Clixen Chat Interaction Patterns Mockup

export interface DemoMessage {
  id: number
  type: "ai" | "user"
  content: string
  timestamp: string
  workflowSummary?: any
  progress?: any[]
  questions?: string[]
  options?: Array<{ label: string; value: string }>
  actionButtons?: Array<{
    label: string
    action: string
    variant?: "default" | "outline" | "destructive" | "secondary"
    icon?: React.ReactNode
  }>
  errorMessage?: string
  samplePreview?: {
    title: string
    content: string
  }
  showSuggestions?: boolean
}

export const DEMO_CONVERSATIONS = {
  // Pattern 1: Simple Direct Request - Weather Workflow
  weatherFlow: [
    {
      id: 1,
      type: "user" as const,
      content: "Send me an email every morning at 8 AM with the weather forecast for New York",
      timestamp: "2:30 PM"
    },
    {
      id: 2,
      type: "ai" as const,
      content: `Perfect! I can create that weather email workflow for you. Here's what I understand:

📋 **Workflow Summary:**
• **Trigger:** Daily at 8:00 AM
• **Action:** Fetch NYC weather data
• **Output:** Send formatted email

Quick questions to finalize:`,
      timestamp: "2:30 PM",
      questions: [
        "What's your email address?",
        "What timezone should I use for 8 AM?",
        "Any specific weather details you want included?"
      ]
    },
    {
      id: 3,
      type: "user" as const,
      content: "My email is john@example.com, use EST timezone, and include temperature, weather conditions, and chance of rain",
      timestamp: "2:32 PM"
    },
    {
      id: 4,
      type: "ai" as const,
      content: `🎯 Excellent! I have everything I need:

📋 **Final Workflow Configuration:**
• **Name:** Daily NYC Weather Email
• **Schedule:** Every day at 8:00 AM EST
• **Weather API:** OpenWeatherMap for NYC
• **Email to:** john@example.com
• **Content:** Temperature, conditions, rain chance

⏳ **Creating your workflow...** (30-60 seconds)`,
      timestamp: "2:32 PM",
      progress: [
        { step: "Weather API configured", status: "completed" },
        { step: "Email template created", status: "completed" },
        { step: "Daily schedule set", status: "completed" },
        { step: "Deploying to n8n...", status: "in-progress", progress: 65 }
      ]
    },
    {
      id: 5,
      type: "ai" as const,
      content: `🎉 **Workflow created successfully!**

Your workflow is now live and will send you daily weather updates every morning!`,
      timestamp: "2:34 PM",
      workflowSummary: {
        name: "Daily NYC Weather Email",
        status: "active",
        trigger: "Every day at 8:00 AM EST",
        action: "Fetch weather data and send email",
        output: "Daily weather email",
        workflowId: "wf_weather_123",
        nextRun: "Tomorrow at 8:00 AM EST"
      },
      samplePreview: {
        title: "Sample email you'll receive:",
        content: `🌤 **NYC Weather - August 9, 2025**

Temperature: 75°F (feels like 78°F)
Conditions: Partly cloudy
Chance of rain: 20%
Have a great day! ☀️`
      },
      actionButtons: [
        { label: "📊 View in Dashboard", action: "view_dashboard", variant: "default" },
        { label: "🔧 Modify Settings", action: "modify_settings", variant: "outline" },
        { label: "🗂 Archive", action: "archive", variant: "secondary" }
      ]
    }
  ],

  // Pattern 2: Complex Multi-Step Workflow - Customer Onboarding
  onboardingFlow: [
    {
      id: 1,
      type: "user" as const,
      content: "I want to automate our customer onboarding process. When someone signs up on our website, send them a welcome email, add them to our CRM, create a task for our sales team, and schedule a follow-up",
      timestamp: "10:15 AM"
    },
    {
      id: 2,
      type: "ai" as const,
      content: `🚀 That's a comprehensive onboarding automation! I love it. Let me break this down and ask some clarifying questions to make sure I get it right:

📋 **What I understand so far:**
1. **Trigger:** New user registration
2. **Welcome email** → New user
3. **Add to CRM** → User data sync
4. **Create task** → Sales team notification
5. **Schedule follow-up** → Future contact

🤔 **Questions to get this perfect:**`,
      timestamp: "10:15 AM",
      questions: [
        "How do I detect new signups? (Webhook, email, database check, etc.)",
        "Which CRM system do you use? (Salesforce, HubSpot, etc.)",
        "How should I notify your sales team? (Email, Slack, CRM task, etc.)",
        "When should the follow-up be scheduled? (1 day, 1 week, etc.)",
        "What info should be included in the welcome email?"
      ]
    },
    {
      id: 3,
      type: "user" as const,
      content: `1. We have a webhook at signup.mysite.com/webhook
2. We use HubSpot CRM
3. Send a Slack message to #sales channel
4. Follow-up should be 3 days after signup
5. Welcome email should include login link and getting started guide`,
      timestamp: "10:18 AM"
    },
    {
      id: 4,
      type: "ai" as const,
      content: `🎯 **Perfect! This is going to be a powerful automation.** Let me map out the complete workflow:

📈 **Customer Onboarding Automation**

**Trigger:**
└─ Webhook: signup.mysite.com/webhook

**Immediate Actions (0 min):**
├─ Send welcome email with login + guide
├─ Add customer to HubSpot CRM
└─ Slack notification to #sales channel

**Delayed Action (3 days):**
└─ Send follow-up email

Before I create this, I need a few more details:`,
      timestamp: "10:19 AM",
      questions: [
        "What's your HubSpot API key? (I'll store securely)",
        "What's your Slack webhook URL for #sales?",
        "From which email should these be sent?",
        "Any specific template for the follow-up email?"
      ]
    }
  ],

  // Pattern 3: Error Handling and Recovery
  errorFlow: [
    {
      id: 1,
      type: "user" as const,
      content: "Create a workflow to backup my Dropbox files to Google Drive every week",
      timestamp: "3:45 PM"
    },
    {
      id: 2,
      type: "ai" as const,
      content: `🔧 I'll create that backup workflow for you...

⏳ **Creating Dropbox to Google Drive backup...**`,
      timestamp: "3:45 PM",
      progress: [
        { step: "Configuring weekly schedule...", status: "completed" },
        { step: "Connecting to Dropbox API...", status: "error" },
        { step: "Waiting for authentication...", status: "pending" }
      ]
    },
    {
      id: 3,
      type: "ai" as const,
      content: `🚨 **Authentication Required**

I need your permission to access your Dropbox and Google Drive accounts. Here's what I need:

🔗 **Dropbox:** [Authorize Access] ←── Click here
🔗 **Google Drive:** [Authorize Access] ←── And here

ℹ️ **Why I need this:**
• Read files from your Dropbox
• Upload backups to your Google Drive
• These permissions are stored securely

Once you authorize both services, I'll complete the workflow setup automatically.`,
      timestamp: "3:46 PM",
      actionButtons: [
        { label: "🔗 Authorize Dropbox", action: "auth_dropbox", variant: "default" },
        { label: "🔗 Authorize Google Drive", action: "auth_gdrive", variant: "default" }
      ]
    }
  ],

  // Pattern 4: Help and Q&A
  helpFlow: [
    {
      id: 1,
      type: "user" as const,
      content: "What's the difference between a webhook and a scheduled trigger?",
      timestamp: "11:20 AM"
    },
    {
      id: 2,
      type: "ai" as const,
      content: `📚 Great question! Let me explain the key differences:

🔗 **Webhook Triggers:**
• **Real-time:** Fire instantly when an event happens
• **Event-driven:** Triggered by external systems
• **Examples:** New form submission, payment received, user signup, order placed
• **Best for:** Immediate responses to user actions

⏰ **Scheduled Triggers:**
• **Time-based:** Run at specific times/intervals
• **Proactive:** Run whether or not something happens
• **Examples:** Daily reports, weekly backups, monthly invoices
• **Best for:** Regular maintenance and reporting

💡 **Which should you use?**
• Need immediate response → Webhook
• Need regular automation → Schedule
• Need both → Hybrid workflow with multiple triggers

Want me to help you create a workflow using either approach? Just describe what you want to automate!`,
      timestamp: "11:20 AM",
      showSuggestions: true
    }
  ],

  // Pattern 5: Workflow Suggestions and Discovery
  suggestionsFlow: [
    {
      id: 1,
      type: "user" as const,
      content: "I'm new to automation. What are some useful workflows I could create for my small business?",
      timestamp: "9:30 AM"
    },
    {
      id: 2,
      type: "ai" as const,
      content: `🚀 **Great question!** Automation can save you hours every week. Here are some popular small business workflows I can help you create:

📧 **Customer Communication:**
• Welcome emails for new customers
• Follow-up surveys after purchases
• Appointment reminders
• Newsletter automation

💰 **Sales & Marketing:**
• Lead capture and CRM sync
• Social media posting
• Invoice generation and reminders
• Customer feedback collection

🛠 **Operations & Admin:**
• Data backup and sync
• Expense tracking and reporting
• Team task assignments
• Inventory monitoring

📊 **Analytics & Reporting:**
• Daily/weekly performance reports
• Website uptime monitoring
• Social media metrics
• Financial dashboards

Which category interests you most? I can create a specific workflow from any of these examples!`,
      timestamp: "9:30 AM",
      options: [
        { label: "Customer Communication - Let's start here", value: "customer_communication" },
        { label: "Sales & Marketing - Perfect for growth", value: "sales_marketing" },
        { label: "Operations & Admin - Essential workflows", value: "operations" },
        { label: "Analytics & Reporting - Data-driven insights", value: "analytics" }
      ]
    }
  ]
}

export function getDemoConversation(pattern: keyof typeof DEMO_CONVERSATIONS): DemoMessage[] {
  return DEMO_CONVERSATIONS[pattern] || []
}

export function getAllDemoPatterns(): string[] {
  return Object.keys(DEMO_CONVERSATIONS)
}
