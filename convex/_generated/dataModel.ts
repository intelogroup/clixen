/* eslint-disable */
/**
 * Generated data model types.
 *
 * THIS CODE IS AUTOMATICALLY GENERATED.
 *
 * To regenerate, run `npx convex dev`.
 * @module
 */

import { AnyDataModel } from "convex/server";
import type { GenericId } from "convex/values";

export interface DataModel extends AnyDataModel {
  users: {
    Document: {
      _id: GenericId<"users">;
      _creationTime: number;
      email: string;
      firstName?: string;
      lastName?: string;
      company?: string;
      avatar?: string;
      plan: "free" | "pro" | "enterprise";
      emailVerified?: boolean;
      onboardingCompleted?: boolean;
      createdAt: number;
      updatedAt: number;
    };
    FieldPaths:
      | "_id"
      | "_creationTime"
      | "email"
      | "firstName"
      | "lastName"
      | "company"
      | "avatar"
      | "plan"
      | "emailVerified"
      | "onboardingCompleted"
      | "createdAt"
      | "updatedAt";
    Indexes: {
      by_email: [string];
    };
    SearchIndexes: {};
    VectorIndexes: {};
  };
  
  workflows: {
    Document: {
      _id: GenericId<"workflows">;
      _creationTime: number;
      userId: GenericId<"users">;
      name: string;
      description?: string;
      type: string;
      trigger: {
        type: "schedule" | "webhook" | "manual";
        config: any;
      };
      actions: Array<{
        type: string;
        config: any;
      }>;
      status: "active" | "paused" | "error";
      lastRun?: number;
      nextRun?: number;
      runCount: number;
      errorCount: number;
      createdAt: number;
      updatedAt: number;
    };
    FieldPaths:
      | "_id"
      | "_creationTime"
      | "userId"
      | "name"
      | "description"
      | "type"
      | "trigger"
      | "trigger.type"
      | "trigger.config"
      | "actions"
      | "status"
      | "lastRun"
      | "nextRun"
      | "runCount"
      | "errorCount"
      | "createdAt"
      | "updatedAt";
    Indexes: {
      by_user: [GenericId<"users">];
      by_status: ["active" | "paused" | "error"];
      by_type: [string];
    };
    SearchIndexes: {};
    VectorIndexes: {};
  };

  workflowRuns: {
    Document: {
      _id: GenericId<"workflowRuns">;
      _creationTime: number;
      workflowId: GenericId<"workflows">;
      userId: GenericId<"users">;
      status: "running" | "completed" | "failed";
      startedAt: number;
      completedAt?: number;
      error?: string;
      logs: Array<{
        timestamp: number;
        level: "info" | "warn" | "error";
        message: string;
      }>;
    };
    FieldPaths:
      | "_id"
      | "_creationTime"
      | "workflowId"
      | "userId"
      | "status"
      | "startedAt"
      | "completedAt"
      | "error"
      | "logs";
    Indexes: {
      by_workflow: [GenericId<"workflows">];
      by_user: [GenericId<"users">];
      by_status: ["running" | "completed" | "failed"];
    };
    SearchIndexes: {};
    VectorIndexes: {};
  };

  chatSessions: {
    Document: {
      _id: GenericId<"chatSessions">;
      _creationTime: number;
      userId: GenericId<"users">;
      title: string;
      messages: Array<{
        id: string;
        type: "user" | "ai";
        content: string;
        timestamp: number;
        metadata?: any;
      }>;
      status: "active" | "archived";
      createdAt: number;
      updatedAt: number;
    };
    FieldPaths:
      | "_id"
      | "_creationTime"
      | "userId"
      | "title"
      | "messages"
      | "status"
      | "createdAt"
      | "updatedAt";
    Indexes: {
      by_user: [GenericId<"users">];
      by_status: ["active" | "archived"];
    };
    SearchIndexes: {};
    VectorIndexes: {};
  };
}
