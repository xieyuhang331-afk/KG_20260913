import { useState } from "react";
import { exitTherapist, resumeTherapist, suspendTherapist } from "../api";

export function TherapistStatusPage() {
  const [message, setMessage] = useState("");
  async function mutate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const id = String(data.get("therapist_id"));
    const version = Number(data.get("expected_version"));
    const operation = String(data.get("operation"));
    try {
      if (operation === "resume") await resumeTherapist(id, version, crypto.randomUUID());
      else if (operation === "exit") await exitTherapist(id, version, "PLATFORM_EXIT", crypto.randomUUID());
      else await suspendTherapist(id, version, "COMPLIANCE_SUSPENDED", crypto.randomUUID());
      setMessage("状态决定已记录");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "状态操作失败");
    }
  }
  return (
    <main>
      <h1 className="text-2xl font-semibold">健管师状态</h1>
      <form onSubmit={mutate} className="mt-4 grid max-w-md gap-2">
        <input name="therapist_id" placeholder="健管师 ID" required className="rounded border p-2" />
        <input name="expected_version" type="number" min="1" required className="rounded border p-2" />
        <select name="operation" className="rounded border p-2">
          <option value="suspend">暂停</option>
          <option value="resume">恢复</option>
          <option value="exit">退出</option>
        </select>
        <button type="submit" className="rounded bg-pine p-2 text-white">
          提交
        </button>
      </form>
      <p role="status">{message}</p>
    </main>
  );
}
