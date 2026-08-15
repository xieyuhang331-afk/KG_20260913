import { useCallback, useEffect, useState } from "react";
import {
  decideInstitutionReview,
  fetchPrivateFileContent,
  getInstitutionReviewDetail,
  listInstitutionReviews,
  requestPrivateFileAccess,
  type InstitutionReviewDecisionPayload,
  type InstitutionReviewView,
} from "../api";

const correctionOptions = [
  ["credit_code", "统一社会信用代码"],
  ["legal_representative_name", "法定代表人"],
  ["registered_address", "注册地址"],
  ["service_address", "服务地址"],
  ["contact_name", "联系人"],
  ["contact_phone", "联系电话"],
  ["contact_email", "联系邮箱"],
  ["service_tags", "服务标签"],
  ["business_license", "营业执照"],
  ["medical_institution_license", "医疗机构执业许可证"],
] as const;

export function InstitutionReviewPage() {
  const [rows, setRows] = useState<InstitutionReviewView[]>([]);
  const [detail, setDetail] = useState<InstitutionReviewView | null>(null);
  const [message, setMessage] = useState("");
  const [reauthPassword, setReauthPassword] = useState("");
  const [selectedCorrectionFields, setSelectedCorrectionFields] = useState<string[]>([]);
  const load = useCallback(
    () =>
      listInstitutionReviews()
        .then(setRows)
        .catch((error) => setMessage(error instanceof Error ? error.message : "审核队列读取失败")),
    [],
  );
  useEffect(() => {
    void load();
  }, [load]);
  async function openDetail(id: string) {
    try {
      setDetail(await getInstitutionReviewDetail(id));
      setSelectedCorrectionFields([]);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "审核详情读取失败");
    }
  }
  async function viewMaterial(fileId: string) {
    try {
      if (!reauthPassword) {
        setMessage("请先输入当前账户密码");
        return;
      }
      const access = await requestPrivateFileAccess(fileId, reauthPassword);
      setReauthPassword("");
      const blob = await fetchPrivateFileContent(access.access_path);
      const objectUrl = URL.createObjectURL(blob);
      window.open(objectUrl, "_blank", "noopener,noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "材料读取失败");
    }
  }
  async function decide(
    id: string,
    decision: InstitutionReviewDecisionPayload["decision"],
    version: number,
    correctionFields?: string[],
  ) {
    const requestedCorrectionFields = correctionFields ?? selectedCorrectionFields;
    if (decision === "NEEDS_CORRECTION" && requestedCorrectionFields.length === 0) {
      setMessage("请至少选择一个补正字段");
      return;
    }
    try {
      await decideInstitutionReview(
        id,
        {
          decision,
          expected_version: version,
          correction_fields: decision === "NEEDS_CORRECTION" ? requestedCorrectionFields : [],
          reason_code: decision === "APPROVED" ? null : "PLATFORM_REVIEW",
        },
        crypto.randomUUID(),
      );
      setDetail(null);
      setSelectedCorrectionFields([]);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "审核决定失败");
    }
  }
  const materials = detail?.materials ?? [];
  const draftFields = [
    ["credit_code", "统一社会信用代码"],
    ["legal_representative_name", "法定代表人"],
    ["registered_address", "注册地址"],
    ["service_address", "服务地址"],
    ["contact_name", "联系人"],
    ["contact_phone", "联系电话"],
    ["contact_email", "联系邮箱"],
    ["service_tags", "服务标签"],
  ] as const;
  return (
    <main>
      <h1 className="text-2xl font-semibold">受控入驻审核</h1>
      <ul className="mt-5 space-y-3">
        {rows.map((row) => (
          <li key={String(row.application_id)} className="rounded border p-4">
            <p>
              {String(row.application_id)} · {String(row.status)}
            </p>
            <button
              type="button"
              onClick={() => void openDetail(String(row.application_id))}
              className="mt-2 rounded border px-3 py-1"
            >
              查看详情
            </button>
          </li>
        ))}
      </ul>
      {detail && (
        <section className="mt-6 rounded border p-4">
          <h2 className="font-semibold">申请详情</h2>
          {draftFields.map(([key, label]) =>
            detail.draft?.[key] == null ? null : (
              <p key={key}>
                {label}：{String(detail.draft[key])}
              </p>
            ),
          )}
          <label className="mt-3 block">
            当前账户密码
            <input
              type="password"
              value={reauthPassword}
              onChange={(event) => setReauthPassword(event.target.value)}
              autoComplete="current-password"
              className="ml-2 rounded border p-2"
            />
          </label>
          <fieldset className="mt-3 rounded border p-3">
            <legend className="px-1 font-medium">选择补正字段</legend>
            <div className="grid gap-2 md:grid-cols-2">
              {correctionOptions.map(([field, label]) => (
                <label key={field} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={selectedCorrectionFields.includes(field)}
                    onChange={(event) =>
                      setSelectedCorrectionFields((current) =>
                        event.target.checked ? [...current, field] : current.filter((value) => value !== field),
                      )
                    }
                  />
                  {label}需补正
                </label>
              ))}
            </div>
          </fieldset>
          {materials.map((material) => (
            <div key={String(material.file_id)}>
              <span>
                {String(material.license_type)} · {String(material.status)}
              </span>
              <button
                type="button"
                onClick={() => void viewMaterial(String(material.file_id))}
                className="ml-2 rounded border px-2 py-1"
              >
                受控查看材料
              </button>
              {(material.license_type === "BUSINESS_LICENSE" ||
                material.license_type === "MEDICAL_INSTITUTION_LICENSE") && (
                <button
                  type="button"
                  onClick={() =>
                    void decide(String(detail.application_id), "NEEDS_CORRECTION", Number(detail.version), [
                      material.license_type === "BUSINESS_LICENSE" ? "business_license" : "medical_institution_license",
                    ])
                  }
                  className="ml-2 rounded border px-2 py-1"
                >
                  要求补正此材料
                </button>
              )}
            </div>
          ))}
          <div className="mt-3 flex gap-2">
            {(["APPROVED", "NEEDS_CORRECTION", "REJECTED"] as const).map((value) => (
              <button
                type="button"
                key={value}
                onClick={() => void decide(String(detail.application_id), value, Number(detail.version))}
                className="rounded border px-3 py-1"
              >
                {value}
              </button>
            ))}
          </div>
        </section>
      )}
      {message && <p role="status">{message}</p>}
    </main>
  );
}
