import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";
import { authTables } from "@convex-dev/auth/server";

export default defineSchema({
  // Auth tables
  ...authTables,

  // Simple messages table for chat (following Convex quickstart)
  messages: defineTable({
    author: v.string(),
    body: v.string(),
  }),

  // User profile table
  users: defineTable({
    email: v.string(),
    firstName: v.optional(v.string()),
    lastName: v.optional(v.string()),
    company: v.optional(v.string()),
    avatar: v.optional(v.string()),
    plan: v.union(v.literal("free"), v.literal("pro"), v.literal("enterprise")),
    emailVerified: v.optional(v.boolean()),
    onboardingCompleted: v.optional(v.boolean()),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_email", ["email"]),

  // Workflows table
  workflows: defineTable({
    userId: v.id("users"),
    name: v.string(),
    description: v.optional(v.string()),
    type: v.string(), // "email", "social", "backup", etc.
    trigger: v.object({
      type: v.union(v.literal("schedule"), v.literal("webhook"), v.literal("manual")),
      config: v.any(),
    }),
    actions: v.array(v.object({
      type: v.string(),
      config: v.any(),
    })),
    status: v.union(v.literal("active"), v.literal("paused"), v.literal("error")),
    lastRun: v.optional(v.number()),
    nextRun: v.optional(v.number()),
    runCount: v.number(),
    errorCount: v.number(),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_user", ["userId"])
    .index("by_status", ["status"])
    .index("by_type", ["type"]),

  // Workflow runs table
  workflowRuns: defineTable({
    workflowId: v.id("workflows"),
    userId: v.id("users"),
    status: v.union(v.literal("running"), v.literal("completed"), v.literal("failed")),
    startedAt: v.number(),
    completedAt: v.optional(v.number()),
    error: v.optional(v.string()),
    logs: v.array(v.object({
      timestamp: v.number(),
      level: v.union(v.literal("info"), v.literal("warn"), v.literal("error")),
      message: v.string(),
    })),
  })
    .index("by_workflow", ["workflowId"])
    .index("by_user", ["userId"])
    .index("by_status", ["status"]),

  // Chat sessions table
  chatSessions: defineTable({
    userId: v.id("users"),
    title: v.string(),
    messages: v.array(v.object({
      id: v.string(),
      type: v.union(v.literal("user"), v.literal("ai")),
      content: v.string(),
      timestamp: v.number(),
      metadata: v.optional(v.any()),
    })),
    status: v.union(v.literal("active"), v.literal("archived")),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_user", ["userId"])
    .index("by_status", ["status"]),
});
