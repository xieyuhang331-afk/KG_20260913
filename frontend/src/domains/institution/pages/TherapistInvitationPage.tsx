import { useCallback, useEffect, useState } from "react";
import { createTherapistInvitation, listTherapistInvitations, revokeTherapistInvitation } from "../api";
import type { TherapistInvitation } from "../types";

export function TherapistInvitationPage() {
  const [items, setItems] = useState<TherapistInvitation[]>([]);
  const [message, setMessage] = useState("");
  const load = useCallback(() => listTherapistInvitations().then((value) => setItems(value.items)), []);
  useEffect(() => {
    void load();
  }, [load]);
  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const value = await createTherapistInvitation(
        { phone: String(data.get("phone")), expires_in_minutes: 60 },
        crypto.randomUUID(),
      );
      setMessage(`邀请已创建，线下短码：${value.short_code ?? ""}`);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建失败");
    }
  }
  return (
    <main>
      <h1 className="text-2xl font-semibold">健管师邀请</h1>
      <form onSubmit={create} className="mt-4 flex gap-2">
        <input name="phone" aria-label="手机号" required pattern="1[0-9]{10}" className="rounded border p-2" />
        <button type="submit" className="rounded bg-pine p-2 text-white">
          创建邀请
        </button>
      </form>
      <p role="status">{message}</p>
      <ul>
        {items.map((item) => (
          <li key={item.invitation_id} className="my-2 rounded border p-3">
            {item.masked_phone} · {item.status}
            {item.status === "INVITED" && (
              <button
                type="button"
                className="ml-3 rounded border px-2"
                onClick={() =>
                  void revokeTherapistInvitation(item.invitation_id, item.version, crypto.randomUUID()).then(load)
                }
              >
                撤销
              </button>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}
