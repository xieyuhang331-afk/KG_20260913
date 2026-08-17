import { useCallback, useEffect, useState } from "react";
import { isApiError } from "@/shared/api/errors";
import { decideTherapistReview, getTherapistReview, listTherapistReviews } from "../api";
import type { TherapistReviewItem, TherapistReviewQualification } from "../types";

export function TherapistReviewPage() {
  const [items, setItems] = useState<TherapistReviewItem[]>([]);
  const [history, setHistory] = useState<TherapistReviewQualification[]>([]);
  const [message, setMessage] = useState("");
  const load = useCallback(() => listTherapistReviews().then((value) => setItems(value.items)), []);
  useEffect(() => {
    void load();
  }, [load]);
  async function decide(item: TherapistReviewItem, decision: "NEEDS_CORRECTION" | "REJECTED" | "APPROVED") {
    try {
      const detail = await getTherapistReview(item.therapist_id);
      const currentIds = detail.current_qualification_ids;
      const uniqueIds = new Set(currentIds);
      const currentQualifications = currentIds.flatMap((id) => {
        const matches = detail.qualifications.filter((qualification) => qualification.qualification_version_id === id);
        return matches.length === 1 ? matches : [];
      });
      const renewalMatches =
        item.review_kind !== "RENEWAL" || (currentIds.length === 1 && item.qualification_version_id === currentIds[0]);
      if (
        currentIds.length === 0 ||
        uniqueIds.size !== currentIds.length ||
        currentQualifications.length !== currentIds.length ||
        !renewalMatches
      ) {
        setMessage("审核资质集合已变化");
        return;
      }
      setHistory(
        detail.qualifications.filter((qualification) => !uniqueIds.has(qualification.qualification_version_id)),
      );
      const qualification_outcomes =
        decision === "NEEDS_CORRECTION"
          ? {}
          : Object.fromEntries(
              currentQualifications.map((qualification) => [
                qualification.qualification_version_id,
                decision === "APPROVED" ? ("APPROVED" as const) : ("REJECTED" as const),
              ]),
            );
      await decideTherapistReview(
        item.therapist_id,
        {
          decision,
          expected_version: item.version,
          reason_code: decision === "APPROVED" ? null : "QUALIFICATION_REVIEW",
          profile_fields: decision === "NEEDS_CORRECTION" ? ["practice_summary"] : [],
          qualification_targets: [],
          qualification_outcomes,
        },
        crypto.randomUUID(),
      );
      setMessage("审核决定已记录");
      await load();
    } catch (error) {
      if (isApiError(error) && error.status === 409) {
        await load();
        setMessage("审核状态已更新，请重新确认");
        return;
      }
      setMessage(error instanceof Error ? error.message : "审核失败");
    }
  }
  return (
    <main>
      <h1 className="text-2xl font-semibold">健管师资质审核</h1>
      <p role="status">{message}</p>
      {history.length > 0 && <p>历史资质 · {history.map((item) => item.issuer_name).join("、")}</p>}
      <ul className="mt-4 space-y-2">
        {items.map((item) => (
          <li key={item.review_item_id} className="rounded border p-3">
            {item.review_kind} · {item.status}
            <span className="ml-3 inline-flex gap-2">
              <button type="button" onClick={() => void decide(item, "APPROVED")}>
                批准
              </button>
              <button type="button" onClick={() => void decide(item, "NEEDS_CORRECTION")}>
                补正
              </button>
              <button type="button" onClick={() => void decide(item, "REJECTED")}>
                驳回
              </button>
            </span>
          </li>
        ))}
      </ul>
    </main>
  );
}
