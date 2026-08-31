import { apiRequest } from "./client";
import { cursorQuery, getSafeApiError, type CursorParams, type SafeApiError } from "./slice3";

export type RiskLevel = "NOT_ASSESSED" | "WITHIN_RANGE" | "ATTENTION" | "HIGH_RISK";
export type AssessmentStatus = "DRAFT_SNAPSHOT" | "RUNNING" | "COMPLETED" | "FAILED" | "UNDER_REVIEW" | "SUPERSEDED";
export type HighRiskTaskStatus = "OPEN" | "CLAIMED" | "ESCALATED" | "REFERRED" | "RESOLVED";
export type ModuleCode =
  | "BLOOD_PRESSURE_CARDIOVASCULAR"
  | "GLUCOSE_METABOLISM"
  | "LIPID_METABOLISM"
  | "WEIGHT_ABDOMINAL_OBESITY";
export type HighRiskActionCode = "CLAIM" | "ESCALATE" | "REFER" | "RESOLVE";
export type RuleSetStatus = "DRAFT" | "IN_REVIEW" | "NEEDS_CORRECTION" | "PUBLISHED" | "SUSPENDED" | "RETIRED";
export type RuleApprovalState = "PENDING" | "APPROVED" | "NEEDS_CORRECTION";
export type RuleReviewReasonCode =
  | "MEDICAL_CONTENT_APPROVED"
  | "RULE_CONTENT_CORRECTION_REQUIRED"
  | "MEDICAL_EVIDENCE_CORRECTION_REQUIRED"
  | "GOLDEN_CASE_CORRECTION_REQUIRED"
  | "HIGH_RISK_SAFETY_CORRECTION_REQUIRED";
export type RuleGovernanceOperation = "SUSPEND" | "RESUME" | "RETIRE";
export type RuleGovernanceReasonCode =
  | "MEDICAL_SAFETY_REVIEW_REQUIRED"
  | "APPROVAL_EVIDENCE_INVALIDATED"
  | "RULE_IMPLEMENTATION_DEFECT_CONFIRMED"
  | "MEDICAL_SAFETY_REVIEW_CLEARED"
  | "APPROVAL_EVIDENCE_REVALIDATED"
  | "RULE_IMPLEMENTATION_DEFECT_REMEDIATED"
  | "SUPERSEDED_BY_APPROVED_VERSION"
  | "BASELINE_WITHDRAWN";

export interface AssessmentSummaryDTO {
  assessment_id: string;
  service_case_id: string;
  sequence_no: number;
  status: AssessmentStatus;
  overall_risk: RiskLevel | null;
  rule_version: string;
  input_snapshot_ref: string;
  supersedes_assessment_id: string | null;
  initiated_at: string;
  completed_at: string | null;
  version: number;
}

export interface AssessmentPageDTO {
  items: AssessmentSummaryDTO[];
  next_cursor: string | null;
}

export interface HighRiskTaskDTO {
  task_id: string;
  assessment_id: string;
  service_case_id: string;
  status: HighRiskTaskStatus;
  reason_module_codes: ModuleCode[];
  assignee_ref: PublicUserRefDTO | null;
  due_at: string;
  last_action_at: string | null;
  blocking: { ordinary_plan: boolean; case_completion: boolean };
  version: number;
  created_at: string;
  closed_at: string | null;
}

export interface PublicUserRefDTO {
  public_user_ref: string;
  display_name: string;
  role_label:
    | "EXPERT"
    | "MEDICAL_REVIEWER"
    | "THERAPIST"
    | "PLATFORM_GOVERNANCE"
    | "INSTITUTION_ADMIN"
    | "INSTITUTION_OPERATOR";
}

export interface ApprovedRuleDefinitionDTO {
  rule_id: string;
  module_code: ModuleCode;
  definition_code: string;
  input_codes: string[];
  unit: "mmHg" | "mmol/L" | "%" | "cm" | "kg" | "kg/m2" | null;
  measurement_contexts: string[];
  risk_levels: RiskLevel[];
  golden_case_refs: string[];
  high_risk_trigger_codes: string[];
}

export interface DeferredRuleReferenceDTO {
  rule_id: string;
  module_code: ModuleCode;
  enabled: false;
  reason_code: "DEFERRED_NOT_IN_V1";
}

export interface MedicalRuleModuleV1DTO {
  module_code: ModuleCode;
  included_rules: ApprovedRuleDefinitionDTO[];
  deferred_rules: DeferredRuleReferenceDTO[];
  golden_case_refs: string[];
}

export interface MedicalRulePayloadV1DTO {
  schema_version: "SLICE5_MEDICAL_RULE_PAYLOAD_V1";
  rule_set_code: "CN_ADULT_BASELINE_V1";
  modules: MedicalRuleModuleV1DTO[];
}

export interface RuleSetVersionDTO {
  rule_set_version_id: string;
  version_no: number;
  status: RuleSetStatus;
  module_metadata: ModuleCode[];
  author_ref: PublicUserRefDTO;
  reviewer_ref: PublicUserRefDTO | null;
  approval_state: RuleApprovalState;
  effective_from: string | null;
  suspended_at: string | null;
  retired_at: string | null;
  version: number;
}

export interface RuleSetVersionDetailDTO extends RuleSetVersionDTO {
  rule_set_code: string;
  typed_rule_payload: MedicalRulePayloadV1DTO;
  approval_evidence_ref: string | null;
}

export interface RuleSetPageDTO {
  items: RuleSetVersionDTO[];
  next_cursor: string | null;
}

export interface RuleSetCreateRequest {
  rule_set_code: "CN_ADULT_BASELINE_V1";
  version_no: number;
  typed_rule_payload: MedicalRulePayloadV1DTO;
  medical_content_digest: string;
  approval_evidence_ref: string;
}

export interface RuleSetDraftUpdateRequest {
  expected_version: number;
  typed_rule_payload: MedicalRulePayloadV1DTO;
  medical_content_digest: string;
  approval_evidence_ref: string;
}

export interface RuleReviewRequest {
  expected_version: number;
  decision: "APPROVE" | "NEEDS_CORRECTION";
  reason_code: RuleReviewReasonCode;
}

export interface RulePublishRequest {
  expected_version: number;
  operation: "PUBLISH";
  reason_code: "DOUBLE_SIGNED_BASELINE_RELEASE";
  effective_from: string;
}

export interface RuleGovernanceRequest {
  expected_version: number;
  operation: RuleGovernanceOperation;
  reason_code: RuleGovernanceReasonCode;
}

export interface HighRiskTaskPageDTO {
  items: HighRiskTaskDTO[];
  next_cursor: string | null;
}

export interface HighRiskTaskActionRequest {
  expected_version: number;
  action_code: HighRiskActionCode;
  contact_outcome_code: "CONTACTED" | "UNABLE_TO_CONTACT" | "NOT_REQUIRED" | null;
  advice_code:
    | "PROMPT_ARTIFICIAL_REVIEW"
    | "PROMPT_MEDICAL_CONTACT"
    | "PROMPT_EMERGENCY_IF_ACUTE"
    | "PROMPT_LOW_GLUCOSE_SAFE_INTAKE"
    | null;
  reason_code:
    | "CLAIMED_FOR_REVIEW"
    | "SAFETY_STATE_UNCONFIRMED"
    | "REFERRED_TO_MEDICAL_RESPONSIBLE_PERSON"
    | "CURRENT_REASSESSMENT_NON_HIGH_RISK"
    | null;
  occurred_at: string;
}

export interface HighRiskTaskListParams extends CursorParams {
  status?: HighRiskTaskStatus;
}

export function getSafeSlice5Error(error: unknown): SafeApiError {
  const safe = getSafeApiError(error);
  return safe.status === 422 ? { ...safe, message: "请求参数或分页凭据不符合要求，请检查后重试" } : safe;
}

export function listInstitutionAssessments(caseId: string, params: CursorParams = {}, signal?: AbortSignal) {
  return noStoreGet<AssessmentPageDTO>(
    `/api/v1/institution/service-cases/${caseId}/assessments${cursorQuery(params)}`,
    signal,
  );
}

export function listInstitutionHighRiskTasks(params: HighRiskTaskListParams = {}) {
  return noStoreGet<HighRiskTaskPageDTO>(`/api/v1/institution/high-risk-tasks${cursorQuery(params)}`);
}

export function getInstitutionHighRiskTask(taskId: string) {
  return noStoreGet<HighRiskTaskDTO>(`/api/v1/institution/high-risk-tasks/${taskId}`);
}

export function actInstitutionHighRiskTask(taskId: string, input: HighRiskTaskActionRequest, key: string) {
  return apiRequest<HighRiskTaskDTO>(`/api/v1/institution/high-risk-tasks/${taskId}/actions`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(input),
  });
}

export function listPlatformHighRiskTasks(params: HighRiskTaskListParams = {}) {
  return noStoreGet<HighRiskTaskPageDTO>(`/api/v1/platform/high-risk-tasks${cursorQuery(params)}`);
}

export function getPlatformHighRiskTask(taskId: string) {
  return noStoreGet<HighRiskTaskDTO>(`/api/v1/platform/high-risk-tasks/${taskId}`);
}

export function listAssessmentRuleSets(params: CursorParams = {}, signal?: AbortSignal) {
  return noStoreGet<RuleSetPageDTO>(`/api/v1/platform/assessment-rule-sets${cursorQuery(params)}`, signal);
}

export function getAssessmentRuleSet(versionId: string, signal?: AbortSignal) {
  return noStoreGet<RuleSetVersionDetailDTO>(`/api/v1/platform/assessment-rule-sets/${versionId}`, signal);
}

export function createAssessmentRuleSet(input: RuleSetCreateRequest, key: string) {
  return ruleMutation("/api/v1/platform/assessment-rule-sets", "POST", input, key);
}

export function updateAssessmentRuleSetDraft(versionId: string, input: RuleSetDraftUpdateRequest, key: string) {
  const body: RuleSetDraftUpdateRequest = {
    expected_version: input.expected_version,
    typed_rule_payload: input.typed_rule_payload,
    medical_content_digest: input.medical_content_digest,
    approval_evidence_ref: input.approval_evidence_ref,
  };
  return ruleMutation(`/api/v1/platform/assessment-rule-sets/${versionId}/draft`, "PATCH", body, key);
}

export function submitAssessmentRuleSet(versionId: string, expectedVersion: number, key: string) {
  return ruleMutation(
    `/api/v1/platform/assessment-rule-sets/${versionId}/submit`,
    "POST",
    { expected_version: expectedVersion },
    key,
  );
}

export function reviewAssessmentRuleSet(versionId: string, input: RuleReviewRequest, key: string) {
  return ruleMutation(`/api/v1/platform/assessment-rule-sets/${versionId}/review`, "POST", input, key);
}

export function publishAssessmentRuleSet(versionId: string, input: RulePublishRequest, key: string) {
  return ruleMutation(`/api/v1/platform/assessment-rule-sets/${versionId}/publish`, "POST", input, key);
}

export function governAssessmentRuleSet(
  versionId: string,
  operation: RuleGovernanceOperation,
  input: RuleGovernanceRequest,
  key: string,
) {
  return ruleMutation(
    `/api/v1/platform/assessment-rule-sets/${versionId}/${operation.toLowerCase()}`,
    "POST",
    input,
    key,
  );
}

export async function medicalRuleDigest(payload: MedicalRulePayloadV1DTO) {
  const bytes = new TextEncoder().encode(canonicalJson(payload));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

function ruleMutation(path: string, method: "POST" | "PATCH", input: object, key: string) {
  return apiRequest<RuleSetVersionDetailDTO>(path, {
    method,
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(input),
  });
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function noStoreGet<T>(path: string, signal?: AbortSignal) {
  return apiRequest<T>(path, { cache: "no-store", signal });
}
