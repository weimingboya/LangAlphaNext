import {
  initialAgentProjection,
  reduceAgentEvent,
} from "../../domain/agent-events";
import type {
  AgentEvent,
  AgentProjection,
  Asset,
  JsonObject,
} from "../../domain/types";

export type ProjectionAction =
  | { type: "reset" }
  | { type: "record"; event: AgentEvent };

export function projectionReducer(
  current: AgentProjection,
  action: ProjectionAction,
): AgentProjection {
  return action.type === "reset"
    ? initialAgentProjection()
    : reduceAgentEvent(current, action.event);
}

export function asPayload(value: unknown): JsonObject {
  return value as JsonObject;
}

export function snapshotEvent(
  threadId: string,
  runId: string,
  type: string,
  payload: JsonObject,
  id: string,
): AgentEvent {
  return {
    id,
    thread_id: threadId,
    run_id: runId,
    type,
    payload,
    created_at: new Date().toISOString(),
  };
}

export function isAsset(value: unknown): value is Asset {
  return Boolean(
    value &&
      typeof value === "object" &&
      "id" in value &&
      "filename" in value &&
      "object_path" in value,
  );
}
