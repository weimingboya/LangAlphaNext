export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export type JsonObject = { [key: string]: JsonValue };

export interface PublicConfig {
  supabase_url: string;
  supabase_publishable_key: string;
  storage_bucket: string;
  max_upload_bytes: number;
}

export interface AuthSession {
  access_token: string;
  refresh_token?: string;
  expires_at?: number;
  expires_in?: number;
  token_type?: string;
  user?: JsonObject;
}

export interface Thread {
  id: string;
  title: string;
  metadata: JsonObject;
  created_at: string;
  updated_at: string;
}

export type RunStatus =
  | "pending"
  | "running"
  | "success"
  | "error"
  | "interrupted"
  | "cancelled";

export interface Run {
  id: string;
  thread_id: string;
  turn_id: string;
  parent_run_id?: string | null;
  status: RunStatus;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export type AssetRole = "input" | "artifact" | "dataset" | "workspace";
export type AssetStatus = "uploading" | "ready" | "failed" | "deleted";

export interface Asset {
  id: string;
  owner_id: string;
  thread_id: string;
  turn_id?: string | null;
  role: AssetRole;
  status: AssetStatus;
  logical_key: string;
  bucket_id: string;
  object_path: string;
  sandbox_path?: string | null;
  filename: string;
  media_type: string;
  size_bytes?: number | null;
  sha256?: string | null;
  retention_class: "temporary" | "standard" | "pinned";
  created_at: string;
  updated_at: string;
}

export interface AssetUploadTicket {
  asset: Asset;
  signed_url: string;
  token: string;
  tus_endpoint: string;
}

export interface AssetDownloadTicket {
  url: string;
  expires_in: number;
}

export interface UsageSummary {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cached_input_tokens: number;
  web_search_calls: number;
  estimated_cost_usd?: number | null;
}

export interface ThreadSnapshot {
  thread: Thread;
  runs: Run[];
  messages: JsonObject[];
  activities?: AgentEvent[];
  todos: JsonObject[];
  interrupts: JsonValue[];
  widgets: Widget[];
  usage: UsageSummary;
  assets: Asset[];
}

export interface AgentEvent {
  id: string;
  thread_id: string;
  run_id: string;
  type: string;
  payload: JsonObject;
  created_at: string;
}

export interface Citation {
  url: string;
  title: string;
  start_index: number | null;
  end_index: number | null;
}

export interface MessageContent {
  text: string;
  citations: Citation[];
}

export interface AgentStatus {
  label: string;
  mode: "idle" | "active" | "error";
}

export interface AgentProjection {
  events: AgentEvent[];
  activeRunId: string | null;
  status: AgentStatus;
}

export interface ActivityItem {
  id: string;
  kind?: "reasoning" | "tool" | "subagent" | "artifact" | "system";
  replaces_id?: string;
  title: string;
  detail?: string;
  status: "running" | "complete" | "error" | "info";
  created_at: string;
}

export interface ProjectedMessage extends AgentEvent {
  author: "You" | "LangAlpha";
  text: string;
  citations?: Citation[];
}

export interface Widget {
  id?: string;
  title?: string;
  description?: string;
  kind?: string;
  x_field?: string;
  y_fields?: string[];
  data?: JsonObject[];
  [key: string]: JsonValue | undefined;
}

export interface ChartSeries {
  field: string;
  values: Array<number | null>;
}

export interface ChartModel {
  kind: "bar" | "line";
  labels: string[];
  series: ChartSeries[];
  minimum: number;
  maximum: number;
}

export type RenderSegment =
  | { kind: "text"; value: string }
  | { kind: "file"; path: string }
  | { kind: "citation"; index: number; url: string; title: string };
