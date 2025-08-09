/* eslint-disable */
/**
 * Generated server utilities.
 *
 * THIS CODE IS AUTOMATICALLY GENERATED.
 *
 * To regenerate, run `npx convex dev`.
 * @module
 */

import {
  actionGeneric,
  httpActionGeneric,
  internalActionGeneric,
  internalMutationGeneric,
  internalQueryGeneric,
  mutationGeneric,
  queryGeneric,
} from "convex/server";
import type { DataModel } from "./dataModel.js";

/**
 * Define a query in this Convex app's public API.
 *
 * This function will be allowed to read your Convex database and will be accessible from the client.
 *
 * @param func - The query function. It receives a `QueryCtx` as its first argument.
 * @returns The wrapped query. Include this as an `export` to add it to your app's API.
 */
export const query = queryGeneric<DataModel>;

/**
 * Define a mutation in this Convex app's public API.
 *
 * This function will be allowed to modify your Convex database and will be accessible from the client.
 *
 * @param func - The mutation function. It receives a `MutationCtx` as its first argument.
 * @returns The wrapped mutation. Include this as an `export` to add it to your app's API.
 */
export const mutation = mutationGeneric<DataModel>;

/**
 * Define an action in this Convex app's public API.
 *
 * An action can run any JavaScript code, including non-deterministic code and code with side-effects,
 * like calling third-party services. They can be run in Convex's JavaScript environment or in Node.js
 * using the "use node" directive. They can interact with the database indirectly by calling queries
 * and mutations using the provided `ActionCtx`. Actions will be accessible from the client.
 *
 * @param func - The action function. It receives an `ActionCtx` as its first argument.
 * @returns The wrapped action. Include this as an `export` to add it to your app's API.
 */
export const action = actionGeneric<DataModel>;

/**
 * Define an HTTP action.
 *
 * This function will be used to respond to HTTP requests received by a Convex
 * app if the request matches the path and method where this action was
 * configured.
 *
 * @param func - The function. It receives an `HttpActionCtx` as its first argument, and a `Request` as its second.
 * @returns The wrapped HTTP action. Include this as an `export` to add it to your app's API.
 */
export const httpAction = httpActionGeneric<DataModel>;

/**
 * Define a query that is only accessible from other Convex functions (but not from the client).
 *
 * This function will be allowed to read from your Convex database. It will not be accessible from the client.
 *
 * @param func - The query function. It receives a `QueryCtx` as its first argument.
 * @returns The wrapped query. Include this as an `export` to add it to your app's API.
 */
export const internalQuery = internalQueryGeneric<DataModel>;

/**
 * Define a mutation that is only accessible from other Convex functions (but not from the client).
 *
 * This function will be allowed to modify your Convex database. It will not be accessible from the client.
 *
 * @param func - The mutation function. It receives a `MutationCtx` as its first argument.
 * @returns The wrapped mutation. Include this as an `export` to add it to your app's API.
 */
export const internalMutation = internalMutationGeneric<DataModel>;

/**
 * Define an action that is only accessible from other Convex functions (but not from the client).
 *
 * @param func - The action function. It receives an `ActionCtx` as its first argument.
 * @returns The wrapped action. Include this as an `export` to add it to your app's API.
 */
export const internalAction = internalActionGeneric<DataModel>;
