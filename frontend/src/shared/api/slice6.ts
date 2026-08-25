import { apiRequest } from "./client";
import {
  createIdempotencyKey,
  cursorQuery,
  getSafeApiError,
  type CursorPage,
  type CursorParams,
  type SafeApiError,
  type UUIDv7,
} from "./slice3";

export type PlanGenerationStatus =
  | "REQUESTED"
  | "GENERATING"
  | "GENERATION_FAILED"
  | "IN_REVIEW"
  | "NEEDS_CORRECTION"
  | "APPROVED"
  | "REJECTED"
  | "USER_DECISION_PENDING"
  | "NEEDS_EXPLANATION"
  | "DECLINED"
  | "ACCEPTED"
  | "ACTIVE"
  | "SUPERSEDED";

export interface PlanGenerationEligibility {
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

export interface PlanGenerationRequest {
  request_id: UUIDv7;
  service_case_id: UUIDv7;
  status: PlanGenerationStatus;
  plan_id: UUIDv7 | null;
  status_reason_codes: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface HealthPlanSummary {
  plan_id: UUIDv7;
  service_case_id: UUIDv7;
  version: number;
  status: PlanGenerationStatus;
  template_code: string;
  template_version: string;
  overall_risk_level: string;
  created_at: string;
  updated_at: string;
}

export interface StructuredPlanItem {
  code: string;
  label?: string;
}

export interface HealthPlanDetail extends HealthPlanSummary {
  module_summaries: StructuredPlanItem[];
  goals: StructuredPlanItem[];
  stages: StructuredPlanItem[];
  milestones: StructuredPlanItem[];
  sop_items: StructuredPlanItem[];
  contraindication_codes: string[];
  user_message_codes: string[];
  therapist_action_codes: string[];
  review_summary: Record<string, unknown> | null;
  user_decision_summary: Record<string, unknown> | null;
  explanations: StructuredPlanItem[];
}

// The following three read models are deliberately isolated until the merged
// OpenAPI freezes their response DTOs. They contain display-only fields and do
// not authorize new request fields.
export interface HealthPlanTemplateSummary {
  template_version_id: UUIDv7;
  template_code: string;
  template_name: string;
  semantic_version: string;
  status: "DRAFT" | "PUBLISHED" | "RETIRED";
  applicable_scope_codes: string[];
  locked_module_codes: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface HealthPlanReviewSummary {
  review_id: UUIDv7;
  plan_id: UUIDv7;
  service_case_id: UUIDv7;
  status: "PENDING_CLAIM" | "IN_REVIEW" | "COMPLETED";
  plan_version: number;
  overall_risk_level: string;
  claimed: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface HealthPlanReviewDetail extends HealthPlanReviewSummary {
  customer_summary_codes: string[];
  assessment_summary_codes: string[];
  plan_summary: {
    template_code: string;
    module_summaries: string[];
  };
  version_diff_codes: string[];
  history: Array<{ action: string; occurred_at: string }>;
}

export interface ReviewDecisionInput {
  decision: "APPROVED" | "NEEDS_CORRECTION" | "REJECTED";
  reason_codes: string[];
  expected_version: number;
}

export function getSafeSlice6Error(error: unknown): SafeApiError {
  const safe = getSafeApiError(error);
  return safe.status === 422 ? { ...safe, message: "请求参数不符合要求，请检查后重试" } : safe;
}

export function getPlanGenerationEligibility(caseId: UUIDv7, signal?: AbortSignal) {
  return apiRequest<PlanGenerationEligibility>(
    `/api/v1/institutions/service-cases/${caseId}/plan-generation-eligibility`,
    { signal },
  );
}

export function createPlanGeneration(
  caseId: UUIDv7,
  expectedVersion: number,
  idempotencyKey: string = createIdempotencyKey(),
) {
  return apiRequest<PlanGenerationRequest>(`/api/v1/institutions/service-cases/${caseId}/plan-generations`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ expected_service_case_version: expectedVersion }),
  });
}

export function getPlanGeneration(requestId: UUIDv7, signal?: AbortSignal) {
  return apiRequest<PlanGenerationRequest>(`/api/v1/institutions/plan-generations/${requestId}`, { signal });
}

export function listInstitutionPlans(caseId: UUIDv7, params: CursorParams = {}, signal?: AbortSignal) {
  return apiRequest<CursorPage<HealthPlanSummary>>(
    `/api/v1/institutions/service-cases/${caseId}/plans${cursorQuery(params)}`,
    { signal },
  );
}

export function getInstitutionPlan(planId: UUIDv7, signal?: AbortSignal) {
  return apiRequest<HealthPlanDetail>(`/api/v1/institutions/plans/${planId}`, { signal });
}

export function listHealthPlanTemplates(params: CursorParams = {}, signal?: AbortSignal) {
  return apiRequest<CursorPage<HealthPlanTemplateSummary>>(
    `/api/v1/platform/health-plan-templates${cursorQuery(params)}`,
    { signal },
  );
}

export function getHealthPlanTemplate(templateVersionId: UUIDv7, signal?: AbortSignal) {
  return apiRequest<HealthPlanTemplateSummary>(`/api/v1/platform/health-plan-templates/${templateVersionId}`, {
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
  return apiRequest<HealthPlanTemplateSummary>(
    `/api/v1/platform/health-plan-templates/${templateVersionId}/${action}`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ expected_version: expectedVersion }),
    },
  );
}

export function listHealthPlanReviews(params: CursorParams & { status?: string } = {}, signal?: AbortSignal) {
  return apiRequest<CursorPage<HealthPlanReviewSummary>>(`/api/v1/platform/health-plan-reviews${cursorQuery(params)}`, {
    signal,
  });
}

export function getHealthPlanReview(reviewId: UUIDv7, signal?: AbortSignal) {
  return apiRequest<HealthPlanReviewDetail>(`/api/v1/platform/health-plan-reviews/${reviewId}`, { signal });
}

export function claimHealthPlanReview(
  reviewId: UUIDv7,
  expectedVersion: number,
  idempotencyKey: string = createIdempotencyKey(),
) {
  return apiRequest<HealthPlanReviewDetail>(`/api/v1/platform/health-plan-reviews/${reviewId}/claim`, {
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
  return apiRequest<HealthPlanReviewDetail>(`/api/v1/platform/health-plan-reviews/${reviewId}/decision`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(input),
  });
}
