import { v } from "convex/values";
import { mutation, query } from "./_generated/server";
import { getAuthUserId } from "@convex-dev/auth/server";

// Get current user
export const getCurrentUser = query({
  args: {},
  handler: async (ctx) => {
    const userId = await getAuthUserId(ctx);
    if (!userId) {
      return null;
    }
    
    return await ctx.db.get(userId);
  },
});

// Get user profile
export const getUserProfile = query({
  args: {},
  handler: async (ctx) => {
    const userId = await getAuthUserId(ctx);
    if (!userId) {
      return null;
    }
    
    // Get user from auth table
    const authUser = await ctx.db.get(userId);
    if (!authUser) {
      return null;
    }
    
    // Get profile from users table
    const profile = await ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", authUser.email ?? ""))
      .first();
    
    return {
      ...authUser,
      ...profile,
    };
  },
});

// Create or update user profile
export const createOrUpdateProfile = mutation({
  args: {
    firstName: v.optional(v.string()),
    lastName: v.optional(v.string()),
    company: v.optional(v.string()),
    plan: v.optional(v.union(v.literal("free"), v.literal("pro"), v.literal("enterprise"))),
    onboardingCompleted: v.optional(v.boolean()),
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
    
    // Check if profile exists
    const existingProfile = await ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", authUser.email))
      .first();
    
    const now = Date.now();
    
    const profileData = {
      email: authUser.email,
      firstName: args.firstName,
      lastName: args.lastName,
      company: args.company,
      plan: args.plan ?? "free" as const,
      onboardingCompleted: args.onboardingCompleted,
      updatedAt: now,
    };
    
    if (existingProfile) {
      // Update existing profile
      await ctx.db.patch(existingProfile._id, profileData);
      return existingProfile._id;
    } else {
      // Create new profile
      return await ctx.db.insert("users", {
        ...profileData,
        emailVerified: authUser.emailVerified,
        createdAt: now,
      });
    }
  },
});

// Update user plan
export const updateUserPlan = mutation({
  args: {
    plan: v.union(v.literal("free"), v.literal("pro"), v.literal("enterprise")),
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
    
    await ctx.db.patch(profile._id, {
      plan: args.plan,
      updatedAt: Date.now(),
    });
    
    return profile._id;
  },
});

// Get user stats
export const getUserStats = query({
  args: {},
  handler: async (ctx) => {
    const userId = await getAuthUserId(ctx);
    if (!userId) {
      return null;
    }
    
    const authUser = await ctx.db.get(userId);
    if (!authUser || !authUser.email) {
      return null;
    }
    
    const profile = await ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", authUser.email))
      .first();
    
    if (!profile) {
      return null;
    }
    
    // Get workflow stats
    const workflows = await ctx.db
      .query("workflows")
      .withIndex("by_user", (q) => q.eq("userId", profile._id))
      .collect();
    
    const activeWorkflows = workflows.filter(w => w.status === "active").length;
    const totalRuns = workflows.reduce((sum, w) => sum + w.runCount, 0);
    
    // Get recent runs
    const recentRuns = await ctx.db
      .query("workflowRuns")
      .withIndex("by_user", (q) => q.eq("userId", profile._id))
      .order("desc")
      .take(10);
    
    return {
      totalWorkflows: workflows.length,
      activeWorkflows,
      totalRuns,
      successfulRuns: recentRuns.filter(r => r.status === "completed").length,
      failedRuns: recentRuns.filter(r => r.status === "failed").length,
      plan: profile.plan,
      memberSince: profile.createdAt,
    };
  },
});
