import { apiRequest } from "./client";
import {
  createIdempotencyKey,
  cursorQuery,
  getSafeApiError,
  type CursorParams,
  type SafeApiError,
  type UUIDv7,
} from "./slice3";

export type ModuleCode =
  | "BLOOD_PRESSURE_CARDIOVASCULAR"
  | "GLUCOSE_METABOLISM"
  | "LIPID_METABOLISM"
  | "WEIGHT_ABDOMINAL_OBESITY";

export type PlanStatus =
  | "REQUESTED"
  | "GENERATING"
  | "GENERATION_FAILED"
  | "IN_REVIEW"
  | "NEEDS_CORRECTION"
  | "USER_DECISION_PENDING"
  | "NEEDS_EXPLANATION"
  | "DECLINED"
  | "REJECTED"
  | "ACTIVE"
  | "SUPERSEDED";

export type ReviewStatus = "PENDING" | "CLAIMED" | "APPROVED" | "NEEDS_CORRECTION" | "REJECTED";
export type UserDecision = "ACCEPT" | "NEEDS_EXPLANATION" | "DECLINE";
export type TemplateStatus = "DRAFT" | "PUBLISHED" | "RETIRED";
export type RiskLevel = "NOT_ASSESSED" | "WITHIN_RANGE" | "ATTENTION" | "HIGH_RISK";

export interface HealthPlanTemplateCreateInput {
  template_code: string;
  applicable_modules: ModuleCode[];
  goals_by_module: Partial<Record<ModuleCode, string[]>>;
  stage_codes: string[];
  milestone_codes: string[];
  sop_codes: string[];
  contraindication_codes: string[];
  user_message_codes: string[];
  therapist_action_codes: string[];
  medical_approval_ref: string;
}

export interface HealthPlanTemplateDTO extends HealthPlanTemplateCreateInput {
  template_version_id: UUIDv7;
  version_no: number;
  status: TemplateStatus;
  created_at: string;
  published_at: string | null;
  retired_at: string | null;
  version: number;
}

export interface HealthPlanTemplatePageDTO {
  items: HealthPlanTemplateDTO[];
  next_cursor: string | null;
}

export interface PlanGenerationEligibilityDTO {
  service_case_id: UUIDv7;
  eligible: boolean;
  blocking_codes: string[];
  current_assessment_id: UUIDv7 | null;
  assessment_version: number | null;
  published_template_version_id: UUIDv7 | null;
  active_generation_request_id: UUIDv7 | null;
  active_plan_id: UUIDv7 | null;
  expected_service_case_version: number;
  evaluated_at: string;
}

export interface PlanGenerationRequestDTO {
  request_id: UUIDv7;
  service_case_id: UUIDv7;
  status: PlanStatus;
  current_plan_id: UUIDv7 | null;
  current_plan_version: number | null;
  failure_code: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface PlanSummaryDTO {
  plan_id: UUIDv7;
  service_case_id: UUIDv7;
  version_no: number;
  status: PlanStatus;
  template_code: string;
  template_version: number;
  overall_risk_level: RiskLevel;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface ModuleSummaryDTO {
  module_code: ModuleCode;
  risk_level: RiskLevel;
}

export interface ReviewSummaryDTO {
  status: ReviewStatus;
  decision_codes: string[];
  decided_at: string | null;
}

export interface UserDecisionSummaryDTO {
  decision: UserDecision | null;
  decided_at: string | null;
}

export interface ExplanationDTO {
  explanation_id: UUIDv7;
  explanation_codes: string[];
  created_at: string;
}

export interface PlanDetailDTO extends PlanSummaryDTO {
  module_summaries: ModuleSummaryDTO[];
  goals: string[];
  stages: string[];
  milestones: string[];
  sop_items: string[];
  contraindication_codes: string[];
  user_message_codes: string[];
  therapist_action_codes: string[];
  review_summary: ReviewSummaryDTO;
  user_decision_summary: UserDecisionSummaryDTO;
  explanations: ExplanationDTO[];
}

export interface PlanPageDTO {
  items: PlanSummaryDTO[];
  next_cursor: string | null;
}

export interface PlanReviewListItemDTO {
  review_id: UUIDv7;
  request_id: UUIDv7;
  plan_id: UUIDv7;
  service_case_id: UUIDv7;
  status: ReviewStatus;
  plan_version_no: number;
  overall_risk_level: RiskLevel;
  claimed_at: string | null;
  decided_at: string | null;
  version: number;
}

export interface ReviewPlanSummaryDTO {
  plan_status: PlanStatus;
  template_code: string;
  template_version: number;
  module_summaries: string[];
  goals: string[];
  stages: string[];
  milestones: string[];
  sop_items: string[];
  contraindication_codes: string[];
  user_message_codes: string[];
  therapist_action_codes: string[];
}

export interface ReviewHistoryEntryDTO {
  action: "PLAN_GENERATED" | "REVIEW_CLAIMED" | "REVIEW_DECIDED";
  plan_version_no: number;
  occurred_at: string;
}

export interface PlanReviewDetailDTO extends PlanReviewListItemDTO {
  customer_summary_codes: string[];
  assessment_summary_codes: string[];
  plan_summary: ReviewPlanSummaryDTO;
  version_diff_codes: string[];
  history: ReviewHistoryEntryDTO[];
  reason_codes: string[];
  user_decision: UserDecision | null;
}

export interface PlanReviewPageDTO {
  items: PlanReviewListItemDTO[];
  next_cursor: string | null;
}

export type ReviewDecision = "APPROVED" | "NEEDS_CORRECTION" | "REJECTED";
export type ReviewReasonCode =
  | "CONTENT_APPROVED"
  | "TEMPLATE_REAPPLY"
  | "DATA_CONTEXT_RECHECK"
  | "MEDICAL_SAFETY_CONFLICT"
  | "TEMPLATE_SCOPE_UNSUITABLE";

export interface ReviewDecisionInput {
  decision: ReviewDecision;
  reason_codes: ReviewReasonCode[];
  expected_version: number;
}

export interface ReviewListParams {
  cursor?: string;
  limit?: number;
  status?: ReviewStatus;
}

export function getSafeSlice6Error(error: unknown): SafeApiError {
  const safe = getSafeApiError(error);
  return safe.status === 422 ? { ...safe, message: "请求参数不符合要求，请检查后重试" } : safe;
}

export function getPlanGenerationEligibility(caseId: UUIDv7, signal?: AbortSignal) {
  return apiRequest<PlanGenerationEligibilityDTO>(
    `/api/v1/institutions/service-cases/${caseId}/plan-generation-eligibility`,
    { signal },
  );
}

export function createPlanGeneration(
  caseId: UUIDv7,
  expectedVersion: number,
  idempotencyKey: string = createIdempotencyKey(),
) {
  return apiRequest<PlanGenerationRequestDTO>(`/api/v1/institutions/service-cases/${caseId}/plan-generations`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ expected_service_case_version: expectedVersion }),
  });
}

export function getPlanGeneration(requestId: UUIDv7, signal?: AbortSignal) {
  return apiRequest<PlanGenerationRequestDTO>(`/api/v1/institutions/plan-generations/${requestId}`, { signal });
}

export function listInstitutionPlans(caseId: UUIDv7, signal?: AbortSignal) {
  return apiRequest<PlanPageDTO>(`/api/v1/institutions/service-cases/${caseId}/plans`, { signal });
}

export function getInstitutionPlan(planId: UUIDv7, signal?: AbortSignal) {
  return apiRequest<PlanDetailDTO>(`/api/v1/institutions/plans/${planId}`, { signal });
}

export function createHealthPlanTemplate(
  input: HealthPlanTemplateCreateInput,
  idempotencyKey: string = createIdempotencyKey(),
) {
  return apiRequest<HealthPlanTemplateDTO>("/api/v1/platform/health-plan-templates", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(input),
  });
}

export function listHealthPlanTemplates(params: CursorParams = {}, signal?: AbortSignal) {
  return apiRequest<HealthPlanTemplatePageDTO>(`/api/v1/platform/health-plan-templates${cursorQuery(params)}`, {
    signal,
  });
}

export function getHealthPlanTemplate(templateVersionId: UUIDv7, signal?: AbortSignal) {
  return apiRequest<HealthPlanTemplateDTO>(`/api/v1/platform/health-plan-templates/${templateVersionId}`, {
    signal,
  });
}

export function publishHealthPlanTemplate(
  templateVersionId: UUIDv7,
  expectedVersion: number,
  idempotencyKey: string = createIdempotencyKey(),
) {
  return templateMutation(templateVersionId, "publish", expectedVersion, idempotencyKey);
}

export function retireHealthPlanTemplate(
  templateVersionId: UUIDv7,
  expectedVersion: number,
  idempotencyKey: string = createIdempotencyKey(),
) {
  return templateMutation(templateVersionId, "retire", expectedVersion, idempotencyKey);
}

function templateMutation(
  templateVersionId: UUIDv7,
  action: "publish" | "retire",
  expectedVersion: number,
  idempotencyKey: string,
) {
  return apiRequest<HealthPlanTemplateDTO>(`/api/v1/platform/health-plan-templates/${templateVersionId}/${action}`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ expected_version: expectedVersion }),
  });
}

export function listHealthPlanReviews(params: ReviewListParams = {}, signal?: AbortSignal) {
  return apiRequest<PlanReviewPageDTO>(`/api/v1/platform/health-plan-reviews${cursorQuery(params)}`, { signal });
}

export function getHealthPlanReview(reviewId: UUIDv7, signal?: AbortSignal) {
  return apiRequest<PlanReviewDetailDTO>(`/api/v1/platform/health-plan-reviews/${reviewId}`, { signal });
}

export function claimHealthPlanReview(
  reviewId: UUIDv7,
  expectedVersion: number,
  idempotencyKey: string = createIdempotencyKey(),
) {
  return apiRequest<PlanReviewDetailDTO>(`/api/v1/platform/health-plan-reviews/${reviewId}/claim`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ expected_version: expectedVersion }),
  });
}

export function decideHealthPlanReview(
  reviewId: UUIDv7,
  input: ReviewDecisionInput,
  idempotencyKey: string = createIdempotencyKey(),
) {
  return apiRequest<PlanReviewDetailDTO>(`/api/v1/platform/health-plan-reviews/${reviewId}/decision`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(input),
  });
}
