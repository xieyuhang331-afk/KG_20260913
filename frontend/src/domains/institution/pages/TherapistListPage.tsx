import { useEffect, useState } from "react";
import { listTherapists } from "../api";
import type { TherapistProfile } from "../types";

export function TherapistListPage() {
  const [items, setItems] = useState<TherapistProfile[]>([]);
  useEffect(() => {
    void listTherapists().then((value) => setItems(value.items));
  }, []);
  return (
    <main>
      <h1 className="text-2xl font-semibold">健管师名单</h1>
      <ul className="mt-4 space-y-2">
        {items.map((item) => (
          <li key={item.therapist_id} className="rounded border p-3">
            {item.display_name} · {item.status} · 在服 {item.active_case_count}/30
          </li>
        ))}
      </ul>
    </main>
  );
}
