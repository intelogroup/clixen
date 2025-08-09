// Conversation Engine for Clixen Chat Patterns
// Handles sophisticated workflow creation flows from the mockup

export interface ConversationState {
  phase: "welcome" | "gathering" | "clarifying" | "creating" | "success" | "error" | "help"
  workflowType?: string
  collectedData: Record<string, any>
  currentStep?: number
  totalSteps?: number
  requiresAuth?: boolean
  errorCount?: number
}

export interface ConversationResponse {
  content: string
  questions?: string[]
  options?: Array<{ label: string; value: string }>
  actionButtons?: Array<{
    label: string
    action: string
    variant?: "default" | "outline" | "destructive" | "secondary"
    icon?: React.ReactNode
  }>
  workflowSummary?: {
    name: string
    trigger: string
    action: string
    output: string
    status?: "active" | "pending" | "error"
    workflowId?: string
    nextRun?: string
  }
  progress?: Array<{
    step: string
    status: "completed" | "in-progress" | "pending" | "error"
    progress?: number
  }>
  samplePreview?: {
    title: string
    content: string
  }
  errorMessage?: string
  nextState: ConversationState
}

export class ConversationEngine {
  private state: ConversationState = {
    phase: "welcome",
    collectedData: {},
    errorCount: 0
  }

  async processMessage(message: string): Promise<ConversationResponse> {
    const detectedIntent = this.detectIntent(message)
    
    switch (this.state.phase) {
      case "welcome":
        return this.handleWelcome(message, detectedIntent)
      case "gathering":
        return this.handleGathering(message, detectedIntent)
      case "clarifying":
        return this.handleClarifying(message, detectedIntent)
      case "creating":
        return this.handleCreating(message, detectedIntent)
      case "error":
        return this.handleError(message, detectedIntent)
      case "help":
        return this.handleHelp(message, detectedIntent)
      default:
        return this.handleWelcome(message, detectedIntent)
    }
  }

  private detectIntent(message: string): {
    type: string
    confidence: number
    entities: Record<string, any>
  } {
    const msg = message.toLowerCase()
    
    // Weather workflow detection
    if (msg.includes("weather") && (msg.includes("email") || msg.includes("send") || msg.includes("notify"))) {
      return {
        type: "weather_notification",
        confidence: 0.9,
        entities: {
          trigger: msg.includes("daily") || msg.includes("morning") ? "daily" : "on-demand",
          location: this.extractLocation(msg),
          time: this.extractTime(msg)
        }
      }
    }

    // Email automation detection
    if (msg.includes("email") && (msg.includes("automation") || msg.includes("campaign") || msg.includes("welcome"))) {
      return {
        type: "email_automation",
        confidence: 0.85,
        entities: {
          trigger: msg.includes("signup") || msg.includes("register") ? "user_signup" : "scheduled",
          emailType: msg.includes("welcome") ? "welcome" : msg.includes("campaign") ? "campaign" : "general"
        }
      }
    }

    // Social media automation
    if (msg.includes("social") && (msg.includes("post") || msg.includes("media") || msg.includes("automation"))) {
      return {
        type: "social_automation",
        confidence: 0.8,
        entities: {
          platforms: this.extractSocialPlatforms(msg),
          contentType: msg.includes("image") ? "image" : msg.includes("video") ? "video" : "text"
        }
      }
    }

    // Customer onboarding
    if (msg.includes("onboarding") || (msg.includes("customer") && msg.includes("process"))) {
      return {
        type: "customer_onboarding",
        confidence: 0.9,
        entities: {
          complexity: "multi_step",
          systems: this.extractSystems(msg)
        }
      }
    }

    // Backup automation
    if (msg.includes("backup") && (msg.includes("files") || msg.includes("data") || msg.includes("cloud"))) {
      return {
        type: "backup_automation",
        confidence: 0.85,
        entities: {
          source: this.extractStorageService(msg, "source"),
          destination: this.extractStorageService(msg, "destination"),
          frequency: this.extractFrequency(msg)
        }
      }
    }

    // General help or questions
    if (msg.includes("help") || msg.includes("how") || msg.includes("what") || msg.includes("?")) {
      return {
        type: "help_question",
        confidence: 0.7,
        entities: {
          topic: this.extractHelpTopic(msg)
        }
      }
    }

    return {
      type: "general",
      confidence: 0.5,
      entities: {}
    }
  }

  private handleWelcome(message: string, intent: any): ConversationResponse {
    switch (intent.type) {
      case "weather_notification":
        return this.startWeatherWorkflow(intent.entities)
      case "email_automation":
        return this.startEmailWorkflow(intent.entities)
      case "social_automation":
        return this.startSocialWorkflow(intent.entities)
      case "customer_onboarding":
        return this.startOnboardingWorkflow(intent.entities)
      case "backup_automation":
        return this.startBackupWorkflow(intent.entities)
      case "help_question":
        return this.provideHelp(intent.entities.topic)
      default:
        return this.requestClarification(message)
    }
  }

  private startWeatherWorkflow(entities: any): ConversationResponse {
    const missingInfo = []
    if (!entities.location) missingInfo.push("location")
    if (!entities.time) missingInfo.push("time")

    if (missingInfo.length === 0) {
      return this.proceedWithCreation("weather_notification", entities)
    }

    this.state = {
      phase: "gathering",
      workflowType: "weather_notification",
      collectedData: entities,
      currentStep: 1,
      totalSteps: 3
    }

    const questions = []
    if (!entities.location) questions.push("What city would you like weather updates for?")
    if (!entities.time) questions.push("What time should I send the daily weather update?")
    questions.push("What's your email address?")

    return {
      content: `Perfect! I can create that weather email workflow for you. Here's what I understand:

📋 **Workflow Summary:**
• **Trigger:** Daily weather updates
• **Action:** Fetch weather data and format email
• **Output:** Send personalized weather email

I just need a few more details to finalize:`,
      questions,
      nextState: this.state
    }
  }

  private startSocialWorkflow(entities: any): ConversationResponse {
    this.state = {
      phase: "gathering",
      workflowType: "social_automation",
      collectedData: entities,
      currentStep: 1,
      totalSteps: 4
    }

    return {
      content: `🚀 Excellent! Social media automation is a great way to maintain consistent engagement. Let me break this down:

📋 **What I understand so far:**
• **Content:** Automated social media posting
• **Platforms:** Multiple social networks
• **Goal:** Consistent content distribution

I have some questions to create the perfect automation:`,
      questions: [
        "Which social platforms would you like to post to? (Facebook, Twitter, Instagram, LinkedIn, etc.)",
        "What type of content will you be posting? (Text, images, videos, links)",
        "How often should posts go out? (Daily, weekly, etc.)",
        "Do you have content ready, or should I help you set up content creation?"
      ],
      actionButtons: [
        { label: "Facebook + Instagram", action: "platforms", variant: "outline" },
        { label: "Professional Networks", action: "platforms_business", variant: "outline" },
        { label: "All Major Platforms", action: "platforms_all", variant: "outline" }
      ],
      nextState: this.state
    }
  }

  private startOnboardingWorkflow(entities: any): ConversationResponse {
    this.state = {
      phase: "gathering",
      workflowType: "customer_onboarding",
      collectedData: entities,
      currentStep: 1,
      totalSteps: 6
    }

    return {
      content: `🚀 That's a comprehensive onboarding automation! I love it. Let me break this down and ask some clarifying questions to make sure I get it right:

📋 **What I understand so far:**
1. **Trigger:** New user registration
2. **Welcome email** → New user
3. **Add to CRM** → User data sync
4. **Create task** → Sales team notification
5. **Schedule follow-up** → Future contact

🤔 **Questions to get this perfect:**`,
      questions: [
        "How do I detect new signups? (Webhook, email, database check, etc.)",
        "Which CRM system do you use? (Salesforce, HubSpot, etc.)",
        "How should I notify your sales team? (Email, Slack, CRM task, etc.)",
        "When should the follow-up be scheduled? (1 day, 1 week, etc.)",
        "What info should be included in the welcome email?"
      ],
      nextState: this.state
    }
  }

  private startBackupWorkflow(entities: any): ConversationResponse {
    this.state = {
      phase: "gathering",
      workflowType: "backup_automation",
      collectedData: entities,
      currentStep: 1,
      totalSteps: 4
    }

    const hasAuth = entities.source === "dropbox" || entities.destination === "google_drive"

    return {
      content: `🔧 I'll create that backup workflow for you! Regular backups are essential for data protection.

📋 **Backup Automation Setup:**
• **Source:** ${entities.source || "To be determined"}
• **Destination:** ${entities.destination || "To be determined"}  
• **Frequency:** ${entities.frequency || "Weekly"}

${hasAuth ? "⚠️ **Authentication Required:** I'll need your permission to access your cloud storage accounts." : ""}

Let me gather the remaining details:`,
      questions: [
        !entities.source ? "Where are your files currently stored? (Dropbox, Google Drive, local folder, etc.)" : null,
        !entities.destination ? "Where would you like to backup to? (Google Drive, Dropbox, AWS S3, etc.)" : null,
        !entities.frequency ? "How often should backups run? (Daily, weekly, monthly)" : null,
        "Any specific file types or folders to include/exclude?"
      ].filter(Boolean) as string[],
      requiresAuth: hasAuth,
      nextState: { ...this.state, requiresAuth: hasAuth }
    }
  }

  private handleGathering(message: string, intent: any): ConversationResponse {
    // Store the user's response
    this.state.collectedData.userResponse = message
    this.state.currentStep = (this.state.currentStep || 1) + 1

    // Move to clarification phase
    this.state.phase = "clarifying"

    return this.generateClarification()
  }

  private handleClarifying(message: string, intent: any): ConversationResponse {
    // Parse user confirmations and proceed to creation
    if (message.toLowerCase().includes("yes") || message.toLowerCase().includes("proceed") || message.toLowerCase().includes("create")) {
      this.state.phase = "creating"
      return this.generateCreationFlow()
    }

    if (message.toLowerCase().includes("no") || message.toLowerCase().includes("change")) {
      this.state.phase = "gathering"
      return {
        content: "No problem! Let me gather the information again. What would you like to change?",
        nextState: this.state
      }
    }

    // Handle specific modifications
    return this.modifyWorkflowSettings(message)
  }

  private handleCreating(message: string, intent: any): ConversationResponse {
    // Simulate workflow creation progress
    return this.simulateWorkflowCreation()
  }

  private handleError(message: string, intent: any): ConversationResponse {
    this.state.errorCount = (this.state.errorCount || 0) + 1

    if (this.state.errorCount > 2) {
      return {
        content: "I'm having persistent issues creating this workflow. Would you like me to try a different approach or connect you with support?",
        actionButtons: [
          { label: "Try Different Approach", action: "retry_different", variant: "outline" },
          { label: "Contact Support", action: "contact_support", variant: "secondary" },
          { label: "Start Over", action: "restart", variant: "destructive" }
        ],
        nextState: this.state
      }
    }

    return this.generateErrorRecovery()
  }

  private generateClarification(): ConversationResponse {
    const workflowType = this.state.workflowType
    
    switch (workflowType) {
      case "weather_notification":
        return {
          content: `🎯 Excellent! I have everything I need:

📋 **Final Workflow Configuration:**
• **Name:** Daily Weather Email
• **Schedule:** Every day at 8:00 AM EST
• **Weather API:** OpenWeatherMap integration
• **Email to:** your specified address
• **Content:** Temperature, conditions, rain chance

⏳ **Creating your workflow...** (30-60 seconds)`,
          progress: [
            { step: "Weather API configured", status: "completed" },
            { step: "Email template created", status: "completed" },
            { step: "Daily schedule set", status: "completed" },
            { step: "Deploying to n8n...", status: "in-progress", progress: 65 }
          ],
          nextState: { ...this.state, phase: "creating" }
        }

      case "social_automation":
        return {
          content: `🎯 **Perfect! This is going to be a powerful automation.** Let me map out the complete workflow:

📈 **Social Media Automation**

**Platforms:** Facebook, Twitter, Instagram, LinkedIn
**Content Type:** Mixed media with smart scheduling
**Frequency:** Daily posts with optimal timing

**Workflow Components:**
├─ Content queue management
├─ Platform-specific formatting
├─ Optimal timing algorithm
└─ Performance analytics

⏳ **I can update this in real-time.** Ready to proceed?`,
          actionButtons: [
            { label: "✅ Yes, create it", action: "proceed", variant: "default" },
            { label: "📝 Modify settings", action: "modify", variant: "outline" }
          ],
          nextState: this.state
        }

      default:
        return {
          content: "Let me confirm the details before creating your workflow...",
          nextState: this.state
        }
    }
  }

  private generateCreationFlow(): ConversationResponse {
    return {
      content: `🔧 **Creating your ${this.state.workflowType?.replace('_', ' ')} workflow...**`,
      progress: [
        { step: "Setting up triggers...", status: "completed" },
        { step: "Configuring integrations...", status: "in-progress", progress: 45 },
        { step: "Testing connections...", status: "pending" },
        { step: "Final deployment...", status: "pending" }
      ],
      nextState: { ...this.state, phase: "creating" }
    }
  }

  private simulateWorkflowCreation(): ConversationResponse {
    // Simulate successful creation
    const workflowId = `wf_${this.state.workflowType}_${Date.now()}`
    
    return {
      content: `🎉 **Workflow created successfully!**

Your ${this.state.workflowType?.replace('_', ' ')} is now live and ready to work for you!`,
      workflowSummary: {
        name: this.getWorkflowName(),
        trigger: this.getTriggerDescription(),
        action: this.getActionDescription(),
        output: this.getOutputDescription(),
        status: "active",
        workflowId,
        nextRun: "Tomorrow at 8:00 AM EST"
      },
      samplePreview: this.getSamplePreview(),
      actionButtons: [
        { label: "📊 View in Dashboard", action: "view_dashboard", variant: "default" },
        { label: "🔧 Modify Settings", action: "modify_settings", variant: "outline" },
        { label: "🗂 Archive", action: "archive", variant: "secondary" }
      ],
      nextState: { phase: "success", workflowType: this.state.workflowType, collectedData: {} }
    }
  }

  private generateErrorRecovery(): ConversationResponse {
    return {
      content: `❌ **Deployment Failed - But I can fix this!**

📋 **Workflow:** ${this.getWorkflowName()}
🚨 **Error:** Authentication timeout

**What went wrong:**
The connection to your external service timed out during setup. This is usually a temporary issue.

🔧 **Here are your options:**`,
      options: [
        { label: "Auto-fix - Retry automatically", value: "auto_retry" },
        { label: "Re-authenticate - Fresh login", value: "reauth" },
        { label: "Manual setup - Step by step", value: "manual" }
      ],
      errorMessage: "Connection timeout during authentication",
      nextState: this.state
    }
  }

  private provideHelp(topic: string): ConversationResponse {
    if (topic.includes("webhook") || topic.includes("trigger")) {
      return {
        content: `📚 Great question! Let me explain the key differences:

🔗 **Webhook Triggers:**
• **Real-time:** Fire instantly when an event happens
• **Event-driven:** Triggered by external systems
• **Examples:** New form submission, payment received, user signup
• **Best for:** Immediate responses to user actions

⏰ **Scheduled Triggers:**
• **Time-based:** Run at specific times/intervals
• **Proactive:** Run whether or not something happens
• **Examples:** Daily reports, weekly backups, monthly invoices
• **Best for:** Regular maintenance and reporting

💡 **Which should you use?**
• Need immediate response → Webhook
• Need regular automation → Schedule
• Need both → Hybrid workflow

Want me to help you create a workflow using either approach?`,
        nextState: { phase: "welcome", collectedData: {} }
      }
    }

    return {
      content: "I'd be happy to help! What specific aspect of workflow automation would you like to learn about?",
      options: [
        { label: "Triggers and Webhooks", value: "triggers" },
        { label: "API Integrations", value: "apis" },
        { label: "Scheduling Options", value: "scheduling" },
        { label: "Error Handling", value: "errors" }
      ],
      nextState: { phase: "help", collectedData: {} }
    }
  }

  // Helper methods for entity extraction
  private extractLocation(message: string): string | null {
    const locationKeywords = ["new york", "nyc", "london", "paris", "tokyo", "san francisco", "los angeles"]
    for (const location of locationKeywords) {
      if (message.includes(location)) return location
    }
    return null
  }

  private extractTime(message: string): string | null {
    const timePattern = /(\d{1,2})\s*(am|pm|AM|PM)/
    const match = message.match(timePattern)
    return match ? match[0] : null
  }

  private extractSocialPlatforms(message: string): string[] {
    const platforms = []
    if (message.includes("facebook")) platforms.push("facebook")
    if (message.includes("twitter") || message.includes("x.com")) platforms.push("twitter")
    if (message.includes("instagram")) platforms.push("instagram")
    if (message.includes("linkedin")) platforms.push("linkedin")
    return platforms
  }

  private extractSystems(message: string): string[] {
    const systems = []
    if (message.includes("hubspot")) systems.push("hubspot")
    if (message.includes("salesforce")) systems.push("salesforce")
    if (message.includes("slack")) systems.push("slack")
    if (message.includes("email")) systems.push("email")
    return systems
  }

  private extractStorageService(message: string, type: "source" | "destination"): string | null {
    if (message.includes("dropbox")) return "dropbox"
    if (message.includes("google drive")) return "google_drive"
    if (message.includes("aws") || message.includes("s3")) return "aws_s3"
    return null
  }

  private extractFrequency(message: string): string | null {
    if (message.includes("daily")) return "daily"
    if (message.includes("weekly")) return "weekly"
    if (message.includes("monthly")) return "monthly"
    return null
  }

  private extractHelpTopic(message: string): string {
    if (message.includes("webhook")) return "webhooks"
    if (message.includes("schedule")) return "scheduling"
    if (message.includes("api")) return "apis"
    if (message.includes("integration")) return "integrations"
    return "general"
  }

  private requestClarification(message: string): ConversationResponse {
    return {
      content: `I'd love to help you create an automation workflow! I can help with many types of automations:

📧 **Email & Communication:**
• Welcome email sequences
• Newsletter automation  
• Customer notifications

📱 **Social Media:**
• Scheduled posting
• Content distribution
• Engagement tracking

🔄 **Data & Integration:**
• File backups and sync
• CRM automation
• API integrations

📊 **Monitoring & Reporting:**
• Performance alerts
• Regular reports
• System monitoring

Could you tell me more specifically what you'd like to automate? For example:
"Send me daily weather emails" or "Backup my files weekly" or "Post to social media automatically"`,
      nextState: { phase: "welcome", collectedData: {} }
    }
  }

  private modifyWorkflowSettings(message: string): ConversationResponse {
    return {
      content: "I can help you modify the workflow settings. What would you like to change?",
      nextState: this.state
    }
  }

  private getWorkflowName(): string {
    switch (this.state.workflowType) {
      case "weather_notification": return "Daily Weather Email"
      case "social_automation": return "Social Media Automation"
      case "customer_onboarding": return "Customer Onboarding Automation"
      case "backup_automation": return "File Backup Automation"
      default: return "Custom Workflow"
    }
  }

  private getTriggerDescription(): string {
    switch (this.state.workflowType) {
      case "weather_notification": return "Daily at 8:00 AM"
      case "social_automation": return "Content queue schedule"
      case "customer_onboarding": return "New user signup webhook"
      case "backup_automation": return "Weekly schedule"
      default: return "Configurable trigger"
    }
  }

  private getActionDescription(): string {
    switch (this.state.workflowType) {
      case "weather_notification": return "Fetch weather data and send email"
      case "social_automation": return "Post to multiple platforms"
      case "customer_onboarding": return "Multi-step onboarding sequence"
      case "backup_automation": return "Copy files to backup location"
      default: return "Custom automation"
    }
  }

  private getOutputDescription(): string {
    switch (this.state.workflowType) {
      case "weather_notification": return "Personalized weather email"
      case "social_automation": return "Scheduled social posts"
      case "customer_onboarding": return "Welcome emails and CRM updates"
      case "backup_automation": return "Secure file backups"
      default: return "Automated results"
    }
  }

  private getSamplePreview(): { title: string; content: string } | undefined {
    switch (this.state.workflowType) {
      case "weather_notification":
        return {
          title: "Sample email you'll receive:",
          content: `🌤 **NYC Weather - August 9, 2025**

Temperature: 75°F (feels like 78°F)
Conditions: Partly cloudy
Chance of rain: 20%
UV Index: 6 (High) - Use SPF 30+
Air Quality: 85 AQI (Moderate)

Have a great day! ☀️🌿`
        }
      default:
        return undefined
    }
  }

  // Public methods for state management
  public getCurrentState(): ConversationState {
    return this.state
  }

  public setState(newState: ConversationState): void {
    this.state = newState
  }

  public reset(): void {
    this.state = {
      phase: "welcome",
      collectedData: {},
      errorCount: 0
    }
  }
}
