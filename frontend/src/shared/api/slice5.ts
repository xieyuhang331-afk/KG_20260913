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
  assignee: number | null;
  due_at: string;
  last_action_at: string | null;
  blocking: { ordinary_plan: boolean; case_completion: boolean };
  version: number;
  created_at: string;
  closed_at: string | null;
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

function noStoreGet<T>(path: string, signal?: AbortSignal) {
  return apiRequest<T>(path, { cache: "no-store", signal });
}
