import { useEffect, useState } from "react";
import { getServiceReadiness, getServiceReadinessEvidence } from "../api";
import type { ServiceReadiness } from "../types";

export function ServiceReadinessPage() {
  const [current, setCurrent] = useState<ServiceReadiness | null>(null);
  const [history, setHistory] = useState<ServiceReadiness[]>([]);
  useEffect(() => {
    void getServiceReadiness().then(setCurrent);
    void getServiceReadinessEvidence().then((value) => setHistory(value.items));
  }, []);
  return (
    <main>
      <h1 className="text-2xl font-semibold">SERVICE_READY</h1>
      {current && (
        <section className="mt-4 rounded border p-4">
          <strong>{current.readiness_status}</strong>
          <p>{current.reason_codes.join("、") || "全部条件满足"}</p>
          <p>合格健管师：{current.qualified_therapist_count}</p>
        </section>
      )}
      <h2 className="mt-6 font-semibold">证据历史</h2>
      <ul>
        {history.map((item) => (
          <li key={item.evidence_version}>
            v{item.evidence_version} · {item.readiness_status}
          </li>
        ))}
      </ul>
    </main>
  );
}
