import { apiRequest } from "./client";
import { cursorQuery, getSafeApiError, type CursorParams, type SafeApiError } from "./slice3";

export type MilestoneCode = "D0" | "D7" | "D14" | "D21" | "D28";
export type MilestoneStatus = "PENDING" | "DUE" | "COMPLETED" | "MISSED" | "INVALIDATED";
export type ServiceLifecycleStatus =
  | "PLAN_PENDING"
  | "ACTIVE"
  | "PAUSED"
  | "CLOSING"
  | "COMPLETED"
  | "WITHDRAWN_BY_USER"
  | "TERMINATED_BY_INSTITUTION"
  | "TRANSFERRED"
  | "UNABLE_TO_CONTACT"
  | "SAFETY_TERMINATED";
export type ClosingReadiness =
  | "NOT_READY"
  | "READY_TO_CLOSE"
  | "BLOCKED_BY_HIGH_RISK"
  | "BLOCKED_BY_MISSING_MILESTONE"
  | "BLOCKED_BY_USER_ACK";
export type TransferStatus =
  | "REQUESTED_BY_USER"
  | "NEW_INSTITUTION_REVIEWING"
  | "ACCEPTED"
  | "OLD_INSTITUTION_CLOSING"
  | "USER_SCOPE_CONFIRMED"
  | "TRANSFERRED"
  | "REJECTED_BY_NEW_INSTITUTION"
  | "CANCELLED_BY_USER";
export type DataScope =
  | "PROFILE"
  | "REPORT"
  | "CANONICAL_FACT"
  | "ASSESSMENT"
  | "APPROVED_PLAN"
  | "MILESTONE"
  | "SERVICE_SUMMARY";
export type ContinuationHandoffStatus =
  | "PENDING_TARGET_ENROLLMENT"
  | "ENROLLMENT_CREATED"
  | "ASSIGNMENT_PENDING"
  | "CONTINUATION_CASE_LINKED";
export type DataExportStatus = "REQUESTED" | "GENERATING" | "READY" | "DOWNLOADED" | "EXPIRED" | "FAILED" | "CANCELLED";

export interface MilestoneDTO {
  milestone_id: string;
  service_case_id: string;
  code: MilestoneCode;
  window_start: string;
  window_end: string;
  status: MilestoneStatus;
  completed_at: string | null;
  record_summary: Record<string, unknown> | null;
  version: number;
}

export interface ServiceFulfillmentDTO {
  service_case_id: string;
  lifecycle_status: ServiceLifecycleStatus;
  risk_flag: "AT_RISK" | null;
  active_plan_id: string | null;
  cycle_anchor_at: string | null;
  current_schedule_version: number | null;
  milestones: MilestoneDTO[];
  open_high_risk_count: number;
  closing_readiness: ClosingReadiness;
  version: number;
}

export interface ServiceFulfillmentPageDTO {
  items: ServiceFulfillmentDTO[];
  next_cursor: string | null;
}

export interface CaseTransitionRequest {
  expected_version: number;
  reason_code: string;
  note?: string;
}

export interface TransferDecisionRequest {
  expected_version: number;
  reason_code: string;
  service_label?: string;
}

export interface TransferSourceCloseRequest {
  expected_version: number;
  summary_id: string;
  risk_disposition: string;
}

export interface TransferDTO {
  transfer_id: string;
  source_service_case_id: string;
  source_tenant_id: string;
  target_tenant_id: string;
  status: TransferStatus;
  requested_scope: DataScope[];
  target_decision: string | null;
  source_closure_status: string | null;
  scope_confirmed_at: string | null;
  transferred_at: string | null;
  version: number;
}

export interface TransferPageDTO {
  items: TransferDTO[];
  next_cursor: string | null;
}

export interface ContinuationHandoffDTO {
  handoff_id: string;
  transfer_id: string;
  source_service_case_id: string;
  source_tenant_id: string;
  target_tenant_id: string;
  subject_member_id: string;
  authorized_scope: DataScope[];
  status: ContinuationHandoffStatus;
  created_at: string;
  linked_enrollment_id: string | null;
  linked_service_case_id: string | null;
  linked_at: string | null;
  version: number;
}

export interface DataExportDTO {
  export_id: string;
  subject_member_id: string;
  status: DataExportStatus;
  requested_scope: DataScope[];
  requested_at: string;
  ready_at: string | null;
  expires_at: string | null;
  downloaded_at: string | null;
  version: number;
}

export interface DataExportPageDTO {
  items: DataExportDTO[];
  next_cursor: string | null;
}

export interface ServiceCaseListParams extends CursorParams {
  status?: ServiceLifecycleStatus;
  risk?: "AT_RISK";
}

export interface TransferListParams extends CursorParams {
  status?: TransferStatus;
}

export interface DataExportListParams extends CursorParams {
  status?: DataExportStatus;
}

export function getSafeSlice7Error(error: unknown): SafeApiError {
  const safe = getSafeApiError(error);
  return safe.status === 422 ? { ...safe, message: "请求参数或分页凭据不符合要求，请检查后重试" } : safe;
}

export function listInstitutionServiceCases(params: ServiceCaseListParams = {}) {
  return noStoreGet<ServiceFulfillmentPageDTO>(`/api/v1/institutions/service-cases${cursorQuery(params)}`);
}

export function getInstitutionServiceCase(caseId: string) {
  return noStoreGet<ServiceFulfillmentDTO>(`/api/v1/institutions/service-cases/${caseId}/fulfillment`);
}

export function pauseInstitutionServiceCase(caseId: string, input: CaseTransitionRequest, key: string) {
  return mutation<ServiceFulfillmentDTO>(`/api/v1/institutions/service-cases/${caseId}/pause`, input, key);
}

export function resumeInstitutionServiceCase(caseId: string, input: CaseTransitionRequest, key: string) {
  return mutation<ServiceFulfillmentDTO>(`/api/v1/institutions/service-cases/${caseId}/resume`, input, key);
}

export function terminateInstitutionServiceCase(caseId: string, input: CaseTransitionRequest, key: string) {
  return mutation<ServiceFulfillmentDTO>(`/api/v1/institutions/service-cases/${caseId}/terminate`, input, key);
}

export function listInstitutionTransfers(params: TransferListParams = {}) {
  return noStoreGet<TransferPageDTO>(`/api/v1/platform/service-transfers${cursorQuery(params)}`);
}

export function getInstitutionTransfer(transferId: string) {
  return noStoreGet<TransferDTO>(`/api/v1/platform/service-transfers/${transferId}`);
}

export function startServiceTransferReview(transferId: string, input: TransferDecisionRequest, key: string) {
  return mutation<TransferDTO>(`/api/v1/institutions/service-transfers/${transferId}/start-review`, input, key);
}

export function acceptServiceTransfer(transferId: string, input: TransferDecisionRequest, key: string) {
  return mutation<TransferDTO>(`/api/v1/institutions/service-transfers/${transferId}/accept`, input, key);
}

export function rejectServiceTransfer(transferId: string, input: TransferDecisionRequest, key: string) {
  return mutation<TransferDTO>(`/api/v1/institutions/service-transfers/${transferId}/reject`, input, key);
}

export function sourceCloseServiceTransfer(transferId: string, input: TransferSourceCloseRequest, key: string) {
  return mutation<TransferDTO>(`/api/v1/institutions/service-transfers/${transferId}/source-close`, input, key);
}

export function getContinuationHandoff(transferId: string) {
  return noStoreGet<ContinuationHandoffDTO>(
    `/api/v1/institutions/service-transfers/${transferId}/continuation-handoff`,
  );
}

export function listPlatformServiceCases(params: ServiceCaseListParams = {}) {
  return noStoreGet<ServiceFulfillmentPageDTO>(`/api/v1/platform/service-fulfillment/cases${cursorQuery(params)}`);
}

export function getPlatformServiceCase(caseId: string) {
  return noStoreGet<ServiceFulfillmentDTO>(`/api/v1/platform/service-fulfillment/cases/${caseId}`);
}

export function safetyTerminateServiceCase(caseId: string, input: CaseTransitionRequest, key: string) {
  return mutation<ServiceFulfillmentDTO>(`/api/v1/platform/service-cases/${caseId}/safety-terminate`, input, key);
}

export function listPlatformTransfers(params: TransferListParams = {}) {
  return noStoreGet<TransferPageDTO>(`/api/v1/platform/service-transfers${cursorQuery(params)}`);
}

export function getPlatformTransfer(transferId: string) {
  return noStoreGet<TransferDTO>(`/api/v1/platform/service-transfers/${transferId}`);
}

export function coordinateServiceTransferClose(transferId: string, input: TransferDecisionRequest, key: string) {
  return mutation<TransferDTO>(`/api/v1/platform/service-transfers/${transferId}/coordinate-close`, input, key);
}

export function listPlatformDataExports(params: DataExportListParams = {}) {
  return noStoreGet<DataExportPageDTO>(`/api/v1/platform/data-exports${cursorQuery(params)}`);
}

export function getPlatformDataExport(exportId: string) {
  return noStoreGet<DataExportDTO>(`/api/v1/platform/data-exports/${exportId}`);
}

function noStoreGet<T>(path: string) {
  return apiRequest<T>(path, { cache: "no-store" });
}

function mutation<T>(path: string, input: object, key: string) {
  return apiRequest<T>(path, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(input),
  });
}
