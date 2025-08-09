import { v } from "convex/values";
import { mutation, query } from "./_generated/server";
import { getAuthUserId } from "@convex-dev/auth/server";

// Get user workflows
export const getUserWorkflows = query({
  args: {
    status: v.optional(v.union(v.literal("active"), v.literal("paused"), v.literal("error"))),
    type: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const userId = await getAuthUserId(ctx);
    if (!userId) {
      return [];
    }
    
    const authUser = await ctx.db.get(userId);
    if (!authUser || !authUser.email) {
      return [];
    }
    
    const profile = await ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", authUser.email))
      .first();
    
    if (!profile) {
      return [];
    }
    
    let query = ctx.db.query("workflows").withIndex("by_user", (q) => q.eq("userId", profile._id));
    
    if (args.status) {
      query = query.filter((q) => q.eq(q.field("status"), args.status));
    }
    
    if (args.type) {
      query = query.filter((q) => q.eq(q.field("type"), args.type));
    }
    
    return await query.order("desc").collect();
  },
});

// Get single workflow
export const getWorkflow = query({
  args: { workflowId: v.id("workflows") },
  handler: async (ctx, args) => {
    const userId = await getAuthUserId(ctx);
    if (!userId) {
      return null;
    }
    
    const workflow = await ctx.db.get(args.workflowId);
    if (!workflow) {
      return null;
    }
    
    // Verify ownership
    const authUser = await ctx.db.get(userId);
    if (!authUser || !authUser.email) {
      return null;
    }
    
    const profile = await ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", authUser.email))
      .first();
    
    if (!profile || workflow.userId !== profile._id) {
      return null;
    }
    
    return workflow;
  },
});

// Create workflow
export const createWorkflow = mutation({
  args: {
    name: v.string(),
    description: v.optional(v.string()),
    type: v.string(),
    trigger: v.object({
      type: v.union(v.literal("schedule"), v.literal("webhook"), v.literal("manual")),
      config: v.any(),
    }),
    actions: v.array(v.object({
      type: v.string(),
      config: v.any(),
    })),
  },
  handler: async (ctx, args) => {
    const userId = await getAuthUserId(ctx);
    if (!userId) {
      throw new Error("Not authenticated");
    }
    
    const authUser = await ctx.db.get(userId);
    if (!authUser || !authUser.email) {
      throw new Error("User not found");
    }
    
    const profile = await ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", authUser.email))
      .first();
    
    if (!profile) {
      throw new Error("Profile not found");
    }
    
    const now = Date.now();
    
    return await ctx.db.insert("workflows", {
      userId: profile._id,
      name: args.name,
      description: args.description,
      type: args.type,
      trigger: args.trigger,
      actions: args.actions,
      status: "active",
      runCount: 0,
      errorCount: 0,
      createdAt: now,
      updatedAt: now,
    });
  },
});

// Update workflow
export const updateWorkflow = mutation({
  args: {
    workflowId: v.id("workflows"),
    name: v.optional(v.string()),
    description: v.optional(v.string()),
    trigger: v.optional(v.object({
      type: v.union(v.literal("schedule"), v.literal("webhook"), v.literal("manual")),
      config: v.any(),
    })),
    actions: v.optional(v.array(v.object({
      type: v.string(),
      config: v.any(),
    }))),
    status: v.optional(v.union(v.literal("active"), v.literal("paused"), v.literal("error"))),
  },
  handler: async (ctx, args) => {
    const userId = await getAuthUserId(ctx);
    if (!userId) {
      throw new Error("Not authenticated");
    }
    
    const workflow = await ctx.db.get(args.workflowId);
    if (!workflow) {
      throw new Error("Workflow not found");
    }
    
    // Verify ownership
    const authUser = await ctx.db.get(userId);
    if (!authUser || !authUser.email) {
      throw new Error("User not found");
    }
    
    const profile = await ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", authUser.email))
      .first();
    
    if (!profile || workflow.userId !== profile._id) {
      throw new Error("Not authorized");
    }
    
    const updateData: any = {
      updatedAt: Date.now(),
    };
    
    if (args.name !== undefined) updateData.name = args.name;
    if (args.description !== undefined) updateData.description = args.description;
    if (args.trigger !== undefined) updateData.trigger = args.trigger;
    if (args.actions !== undefined) updateData.actions = args.actions;
    if (args.status !== undefined) updateData.status = args.status;
    
    await ctx.db.patch(args.workflowId, updateData);
    
    return args.workflowId;
  },
});

// Delete workflow
export const deleteWorkflow = mutation({
  args: { workflowId: v.id("workflows") },
  handler: async (ctx, args) => {
    const userId = await getAuthUserId(ctx);
    if (!userId) {
      throw new Error("Not authenticated");
    }
    
    const workflow = await ctx.db.get(args.workflowId);
    if (!workflow) {
      throw new Error("Workflow not found");
    }
    
    // Verify ownership
    const authUser = await ctx.db.get(userId);
    if (!authUser || !authUser.email) {
      throw new Error("User not found");
    }
    
    const profile = await ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", authUser.email))
      .first();
    
    if (!profile || workflow.userId !== profile._id) {
      throw new Error("Not authorized");
    }
    
    await ctx.db.delete(args.workflowId);
    
    return args.workflowId;
  },
});

// Get workflow runs
export const getWorkflowRuns = query({
  args: { 
    workflowId: v.optional(v.id("workflows")),
    limit: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const userId = await getAuthUserId(ctx);
    if (!userId) {
      return [];
    }
    
    const authUser = await ctx.db.get(userId);
    if (!authUser || !authUser.email) {
      return [];
    }
    
    const profile = await ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", authUser.email))
      .first();
    
    if (!profile) {
      return [];
    }
    
    let query = ctx.db.query("workflowRuns").withIndex("by_user", (q) => q.eq("userId", profile._id));
    
    if (args.workflowId) {
      query = query.filter((q) => q.eq(q.field("workflowId"), args.workflowId));
    }
    
    return await query.order("desc").take(args.limit ?? 50);
  },
});
