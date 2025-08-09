import { v } from "convex/values";
import { mutation, query } from "./_generated/server";

// Send a message to the chat
export const send = mutation({
  args: { 
    body: v.string(),
    author: v.string()
  },
  handler: async (ctx, args) => {
    await ctx.db.insert("messages", { body: args.body, author: args.author });
  },
});

// Get all messages from the chat
export const list = query({
  handler: async (ctx) => {
    return await ctx.db.query("messages").collect();
  },
});
