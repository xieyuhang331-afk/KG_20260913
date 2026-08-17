import { describe, expect, it } from "vitest";
import { institutionNavigation } from "./navigation";
import type { TherapistQualification } from "./types";

describe("健管师邀请与资格", () => {
  it("接入邀请名单与SERVICE_READY三个真实页面", () => {
    expect(institutionNavigation.map((item) => item.path)).toEqual(
      expect.arrayContaining([
        "/institution/therapist-invitations",
        "/institution/therapists",
        "/institution/service-readiness",
      ]),
    );
  });

  it("资格DTO与后端字段及可空性精确对齐", () => {
    const value: TherapistQualification = {
      qualification_version_id: "00000000-0000-7000-8000-000000000001",
      qualification_type: "METABOLIC_HEALTH_PRACTICE",
      masked_certificate_no: "****1234",
      issuer_name: "issuer",
      valid_from: "2026-01-01",
      valid_until: "2027-01-01",
      derived_review_status: "SUBMITTED",
      attachment_count: 1,
      version_no: 1,
    };
    expect(Object.keys(value).sort()).toEqual([
      "attachment_count",
      "derived_review_status",
      "issuer_name",
      "masked_certificate_no",
      "qualification_type",
      "qualification_version_id",
      "valid_from",
      "valid_until",
      "version_no",
    ]);
  });
});
