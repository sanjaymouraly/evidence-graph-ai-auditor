export type Role = "admin" | "reviewer" | "auditor" | "viewer";

export type SessionUser = {
  id: string;
  name: string;
  email: string;
  role: Role;
};

export type Workspace = {
  id: string;
  name: string;
  slug: string;
  settings?: Record<string, unknown>;
};

export type Paged<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
};

export type DashboardPayload = {
  documents: { total: number; versions: number; processed: number; failed: number; expired: number; expiring_60d: number };
  claims: { total: number; by_status: Record<string, number>; duplicates: number };
  contradictions: { total: number; confirmed: number; possible: number; blocking: number };
  decisions: {
    total: number;
    by_status: Record<string, number>;
    by_gate: Record<string, number>;
    awaiting_review: number;
    approved: number;
  };
  evidence_health_score: number;
  recent_decisions: DecisionSummary[];
  audit_tail?: AuditEvent[];
  top_predicates?: { predicate: string; count: number }[];
};

export type Version = {
  id: string;
  version_number: number;
  filename: string;
  mime_type: string;
  content_hash: string;
  size_bytes: number;
  status: string;
  error?: string | null;
  warnings?: string[];
  is_current: boolean;
  superseded_by_version_id?: string | null;
  effective_date?: string | null;
  issued_date?: string | null;
  valid_until?: string | null;
  source_author?: string | null;
  source_org?: string | null;
  primary_entity?: string | null;
  trust_level?: number;
  injection_flags?: { span: number; flags: string[]; excerpt?: string }[];
  uploaded_at?: string;
  processed_at?: string | null;
};

export type DocumentSummary = {
  id: string;
  doc_key?: string;
  title: string;
  doc_type: string;
  description?: string;
  created_at?: string;
  version_count: number;
  claim_count: number;
  current_version: Version | null;
};

export type DocumentDetail = DocumentSummary & {
  versions: Version[];
  entities?: { id: string; canonical_name: string; entity_type: string }[];
};

export type Span = {
  id: string;
  locator_type: string;
  page?: number | null;
  paragraph_index?: number | null;
  sheet?: string | null;
  cell_range?: string | null;
  row_label?: string | null;
  char_start?: number | null;
  char_end?: number | null;
  bbox?: number[] | null;
  section_label?: string | null;
  text: string;
  text_hash?: string;
};

export type Claim = {
  id: string;
  predicate: string;
  predicate_label: string;
  subject: string;
  entity_id?: string | null;
  object: string;
  value_numeric?: number | null;
  value_text?: string | null;
  unit?: string | null;
  currency?: string | null;
  scope?: string | null;
  qualifiers?: Record<string, unknown>;
  polarity: number;
  effective_date?: string | null;
  valid_until?: string | null;
  observed_date?: string | null;
  extraction_method: string;
  extraction_confidence: number;
  verification_status: string;
  review_status: string;
  review_note?: string | null;
  quoted_span: string;
  duplicate_of_id?: string | null;
  injection_flag?: boolean;
  meta?: Record<string, unknown>;
  document?: { id: string; title: string; doc_type: string } | null;
  version?: { id: string; version_number: number; filename: string; content_hash: string; is_current: boolean } | null;
  span?: Span | null;
  citation?: { resolves: boolean; reason?: string };
};

export type ContradictionSide = {
  claim_id: string;
  predicate?: string;
  subject?: string;
  object?: string;
  quoted_span?: string;
  verification_status?: string;
  document_id?: string;
  document_title?: string;
  doc_type?: string;
  version_id?: string;
  version_number?: number;
  filename?: string;
  locator?: { page?: number | null; sheet?: string | null; cell_range?: string | null; paragraph_index?: number | null; char_start?: number | null };
  missing?: boolean;
};

export type Contradiction = {
  id: string;
  kind: string;
  state: string;
  severity: string;
  confidence: number;
  blocking: boolean;
  reasoning: string;
  detected_by: string;
  review_note?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  created_at: string;
  claim_a: ContradictionSide;
  claim_b: ContradictionSide;
};

export type GateCheck = {
  code: string;
  label: string;
  status: "pass" | "fail" | "warn" | string;
  blocking: boolean;
  detail: string;
  refs?: { type: string; id: string }[];
  evaluated_at?: string;
};

export type GateRequirement = {
  key?: string;
  label: string;
  pass: boolean;
  missing_doc_types?: string[];
  options?: { option: string; pass: boolean; detail?: string }[];
};

export type DecisionOption = { name: string; entity_id?: string; manual_scores?: Record<string, number> };

export type DecisionSummary = {
  id: string;
  title: string;
  decision_type: string;
  status: string;
  gate_status: string | null;
  description: string;
  as_of_date: string | null;
  due_date: string | null;
  options: DecisionOption[];
  selected_option?: string | null;
  documents_count: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  gate_evaluated_at: string | null;
  blocking_failures: (string | { code: string; label?: string })[];
  warnings: (string | { code: string; label?: string })[];
  summary?: { passed?: number; failed?: number; warnings?: number; checks?: number } | null;
  exception_recorded: boolean;
  exception_reason?: string | null;
  reviews?: { action: string; comment?: string; created_at: string }[];
};

export type AnalysisStatement = {
  id: string;
  text: string;
  kind: string;
  claim_ids: string[];
  supported: boolean;
  structural?: boolean;
  excluded?: boolean;
  validation_notes?: string[];
};

export type Analysis = {
  id: string;
  decision_id?: string;
  status: string;
  provider_label: string;
  model?: string | null;
  summary: string;
  recommendation?: string | null;
  statements: AnalysisStatement[];
  validation?: Record<string, unknown>;
  created_at: string;
  unsupported?: number;
  citations?: { claim_id: string; document_title: string; locator: string; quote: string }[];
};

export type GateResult = {
  gate_version?: string;
  policy_version?: string;
  status?: string;
  as_of?: string;
  result_hash?: string;
  checks?: GateCheck[];
  requirements?: GateRequirement[];
  blocking_failures?: (string | { code: string; label?: string })[];
  warnings?: (string | { code: string; label?: string })[];
  mcda?: {
    applicable: boolean;
    criteria: { code: string; label: string; weight: number; direction: string; kind: string }[];
    metrics?: {
      option: string;
      entity_id?: string;
      price?: number;
      delivery?: number;
      warranty?: number;
      risk?: number;
      compliance?: number;
      technical_fit?: number;
      manual_scores?: Record<string, number>;
    }[];
    ranking?: { option: string; score: number; rank?: number }[];
    sensitivity?: { criterion?: string; option?: string; perturbation?: string; ranking?: string[]; top_option_changed?: boolean; baseline?: number; alternative?: number }[];
    explanation?: string;
  };
  summary?: { passed?: number; failed?: number; warnings?: number; checks?: number };
};

export type DecisionDetail = DecisionSummary & {
  documents: DocumentSummary[];
  approvals: {
    id: string;
    action: string;
    comment: string;
    user: SessionUser | null;
    gate_snapshot_hash?: string | null;
    signature_hash?: string | null;
    created_at: string;
  }[];
  analyses: Analysis[];
  gate_result: GateResult;
  gate_checks: GateCheck[];
  criteria: { code: string; label: string; weight: number; direction: string; kind: string }[];
  requirements: GateRequirement[];
  policy_snapshot?: Record<string, unknown>;
  gate_checks_persisted?: GateCheck[];
};

export type Checklist = {
  decision_id: string;
  status: string;
  gate_status: string;
  gate_version: string;
  policy_version: string;
  as_of: string;
  evaluated_at: string;
  checks: GateCheck[];
  requirements: GateRequirement[];
  blocking_failures: (string | { code: string; label?: string })[];
  warnings: (string | { code: string; label?: string })[];
  eligible_for_approval: boolean;
  summary?: { passed?: number; failed?: number; warnings?: number; checks?: number };
};

export type AuditEvent = {
  id: string;
  action: string;
  object_type: string;
  object_id: string;
  actor?: string;
  payload?: Record<string, unknown>;
  prev_hash?: string;
  event_hash?: string;
  correlation_id?: string | null;
  created_at: string;
};

export type ReportRecord = {
  id: string;
  decision_id: string;
  snapshot_id: string;
  kind: string;
  filename: string;
  sha256: string;
  created_at: string;
  download_url?: string;
  decision_title?: string;
  snapshot_verification?: { hash_match: boolean; errors?: string[] };
};

export type BenchmarkTarget = { target: number; actual: number | null; pass: boolean };

export type BenchmarkRun = {
  id: string;
  dataset: string;
  status: string;
  duration_seconds: number;
  created_at: string;
  metrics?: Record<string, unknown>;
  confusion?: Record<string, unknown>;
};

export type BenchmarkResult = {
  run_id?: string;
  dataset: string;
  status: string;
  started_at: string;
  duration_seconds: number;
  corpus: Record<string, unknown>;
  metrics: Record<string, number | null | Record<string, number>>;
  targets: Record<string, BenchmarkTarget>;
  confusion: Record<string, Record<string, Record<string, number>>>;
  details: Record<string, unknown>;
  errors: { stage?: string; decision?: string; error: string }[];
  environment?: Record<string, unknown>;
  harness?: Record<string, unknown>;
};

export type Provider = {
  id: string;
  name: string;
  kind: string;
  base_url?: string;
  model?: string;
  embedding_model?: string;
  rerank_model?: string;
  api_key_set: boolean;
  api_key_hint?: string;
  enabled: boolean;
  is_default_generation?: boolean;
  is_default_embedding?: boolean;
  is_default_rerank?: boolean;
  last_check?: { ok: boolean; latency_ms?: number; error?: string; checked_at?: string; models?: string[] } | null;
  created_at?: string;
};

export type GroundedAnswer = {
  question: string;
  answer: string;
  statements: AnalysisStatement[];
  citations: { claim_id: string; document_id: string; document_title: string; version_id: string; quote: string; locator: string; verification_status: string; predicate: string; value: string }[];
  insufficient_evidence: boolean;
  search_method: string;
  provider: string | null;
  validation?: Record<string, unknown>;
  invalid_citations?: { claim_id: string; reason: string }[];
  fallback_reason?: string | null;
};

export type GraphNode = {
  id: string;
  key?: string;
  type: string;
  label: string;
  status?: string;
  meta?: Record<string, unknown>;
  [key: string]: unknown;
};

export type GraphEdge = {
  id?: string;
  key?: string;
  source: string;
  target: string;
  relation: string;
  label?: string;
  confidence?: number;
};

export type GraphPayload = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  counts: { nodes: number; edges: number; truncated?: boolean };
  truncated?: boolean;
  focus?: string | null;
  node_types?: Record<string, number>;
  relations?: Record<string, number>;
  available_queries?: string[];
};
