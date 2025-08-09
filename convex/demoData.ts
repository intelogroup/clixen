import { mutation } from "./_generated/server";
import { v } from "convex/values";

// Create demo workflows for testing
export const createDemoWorkflows = mutation({
  args: { userEmail: v.string() },
  handler: async (ctx, args) => {
    // Find user by email
    const user = await ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", args.userEmail))
      .first();
    
    if (!user) {
      throw new Error("User not found");
    }

    const now = Date.now();

    // Demo workflows
    const demoWorkflows = [
      {
        name: "Daily Weather Email",
        description: "Get daily weather updates for New York every morning at 8 AM",
        type: "email",
        trigger: {
          type: "schedule" as const,
          config: { schedule: "0 8 * * *", timezone: "America/New_York" }
        },
        actions: [
          {
            type: "fetch_weather",
            config: { location: "New York, NY", apiKey: "weather_api_key" }
          },
          {
            type: "send_email",
            config: { 
              to: args.userEmail,
              template: "weather_daily",
              subject: "Your Daily Weather Update"
            }
          }
        ],
        status: "active" as const,
        runCount: 15,
        errorCount: 0,
        lastRun: now - 86400000, // 1 day ago
        nextRun: now + 43200000, // 12 hours from now
      },
      {
        name: "Social Media Automation",
        description: "Post motivational quotes to Instagram and LinkedIn daily",
        type: "social",
        trigger: {
          type: "schedule" as const,
          config: { schedule: "0 9 * * *", timezone: "America/New_York" }
        },
        actions: [
          {
            type: "generate_quote",
            config: { category: "motivation", source: "api" }
          },
          {
            type: "post_instagram",
            config: { account: "@user_account", includeHashtags: true }
          },
          {
            type: "post_linkedin",
            config: { profile: "user_profile", addProfessionalTags: true }
          }
        ],
        status: "active" as const,
        runCount: 8,
        errorCount: 1,
        lastRun: now - 129600000, // 1.5 days ago
        nextRun: now + 39600000, // 11 hours from now
      },
      {
        name: "Backup to Google Drive",
        description: "Weekly backup of important files from Dropbox to Google Drive",
        type: "backup",
        trigger: {
          type: "schedule" as const,
          config: { schedule: "0 2 * * 0", timezone: "America/New_York" } // Sundays at 2 AM
        },
        actions: [
          {
            type: "scan_dropbox",
            config: { folder: "/Important", includeSubfolders: true }
          },
          {
            type: "sync_to_gdrive",
            config: { 
              destination: "/Backups/Dropbox",
              createTimestampFolder: true,
              preserveStructure: true
            }
          },
          {
            type: "send_notification",
            config: {
              email: args.userEmail,
              subject: "Weekly Backup Complete",
              template: "backup_summary"
            }
          }
        ],
        status: "active" as const,
        runCount: 4,
        errorCount: 0,
        lastRun: now - 259200000, // 3 days ago
        nextRun: now + 345600000, // 4 days from now
      },
      {
        name: "Customer Onboarding",
        description: "Automated welcome sequence for new customers with CRM integration",
        type: "crm",
        trigger: {
          type: "webhook" as const,
          config: { 
            url: "https://api.clixen.com/webhooks/new-customer",
            method: "POST",
            authentication: "bearer"
          }
        },
        actions: [
          {
            type: "send_welcome_email",
            config: {
              template: "welcome_series_1",
              delay: 0,
              personalizeWithData: true
            }
          },
          {
            type: "add_to_crm",
            config: {
              system: "hubspot",
              pipeline: "customers",
              assignToSalesRep: true
            }
          },
          {
            type: "schedule_followup",
            config: {
              delay: 259200000, // 3 days
              type: "email",
              template: "followup_check_in"
            }
          }
        ],
        status: "paused" as const,
        runCount: 2,
        errorCount: 0,
        lastRun: now - 432000000, // 5 days ago
      },
      {
        name: "Website Monitor",
        description: "Monitor website uptime and send alerts when issues are detected",
        type: "monitoring",
        trigger: {
          type: "schedule" as const,
          config: { schedule: "*/5 * * * *", timezone: "UTC" } // Every 5 minutes
        },
        actions: [
          {
            type: "check_website",
            config: {
              url: "https://mysite.com",
              timeout: 10000,
              expectedStatus: 200
            }
          },
          {
            type: "conditional_alert",
            config: {
              condition: "if_down",
              alertChannels: ["email", "slack"],
              escalation: true
            }
          }
        ],
        status: "error" as const,
        runCount: 1440, // Many runs (every 5 minutes)
        errorCount: 3,
        lastRun: now - 300000, // 5 minutes ago
        nextRun: now + 300000, // 5 minutes from now
      }
    ];

    const createdWorkflows = [];
    
    for (const workflow of demoWorkflows) {
      const workflowId = await ctx.db.insert("workflows", {
        userId: user._id,
        ...workflow,
        createdAt: now - Math.random() * 86400000 * 7, // Random time in last week
        updatedAt: now - Math.random() * 86400000 * 2, // Random time in last 2 days
      });
      createdWorkflows.push(workflowId);
    }

    return createdWorkflows;
  },
});

// Create demo workflow runs for testing
export const createDemoWorkflowRuns = mutation({
  args: { userEmail: v.string() },
  handler: async (ctx, args) => {
    // Find user by email
    const user = await ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", args.userEmail))
      .first();
    
    if (!user) {
      throw new Error("User not found");
    }

    // Get user's workflows
    const workflows = await ctx.db
      .query("workflows")
      .withIndex("by_user", (q) => q.eq("userId", user._id))
      .collect();

    const now = Date.now();
    const createdRuns = [];

    for (const workflow of workflows) {
      // Create 3-5 runs per workflow
      const runCount = Math.floor(Math.random() * 3) + 3;
      
      for (let i = 0; i < runCount; i++) {
        const startTime = now - Math.random() * 86400000 * 7; // Random time in last week
        const duration = Math.random() * 30000 + 1000; // 1-31 seconds
        const isSuccess = Math.random() > 0.1; // 90% success rate
        
        const runId = await ctx.db.insert("workflowRuns", {
          workflowId: workflow._id,
          userId: user._id,
          status: isSuccess ? "completed" : "failed",
          startedAt: startTime,
          completedAt: startTime + duration,
          error: isSuccess ? undefined : "Connection timeout to external service",
          logs: [
            {
              timestamp: startTime,
              level: "info" as const,
              message: "Workflow run started"
            },
            {
              timestamp: startTime + 1000,
              level: "info" as const,
              message: "Executing trigger"
            },
            ...(isSuccess ? [
              {
                timestamp: startTime + 5000,
                level: "info" as const,
                message: "Action 1 completed successfully"
              },
              {
                timestamp: startTime + duration,
                level: "info" as const,
                message: "Workflow completed successfully"
              }
            ] : [
              {
                timestamp: startTime + 5000,
                level: "error" as const,
                message: "Connection timeout to external service"
              },
              {
                timestamp: startTime + duration,
                level: "error" as const,
                message: "Workflow failed"
              }
            ])
          ]
        });
        
        createdRuns.push(runId);
      }
    }

    return createdRuns;
  },
});
